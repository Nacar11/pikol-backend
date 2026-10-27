from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.persistence.models import TokenPurpose, UserTokenModel
from src.auth.scope import VenueScope
from src.auth.security import hash_password, verify_password
from src.auth.service import auth_service, insert_user
from src.parameters.service import parameter_service
from src.users.domain.groups import GroupKind
from src.users.domain.roles import RoleCode
from src.users.dto import AdminUserUpdate, PasswordChangeRequest, ProfileUpdate
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.utils.errors import AppError
from src.venues.persistence.models import VenueModel


def not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "User not found.")


def already_accepted() -> AppError:
    return AppError(409, "ALREADY_ACCEPTED", "This invitation has already been accepted.")


class Invitation:
    """What the controller needs to send an invitation email."""

    def __init__(self, user: UserModel, token: str, expiry_days: int) -> None:
        self.user = user
        self.token = token
        self.expiry_days = expiry_days


class UserService:
    async def update_profile(
        self, session: AsyncSession, user: UserModel, body: ProfileUpdate
    ) -> UserModel:
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await session.commit()
        await session.refresh(user, ["role"])
        return user

    async def change_password(
        self, session: AsyncSession, user: UserModel, body: PasswordChangeRequest
    ) -> None:
        await session.refresh(user, ["password_hash"])
        if not verify_password(body.current_password, user.password_hash):
            raise AppError(400, "WRONG_PASSWORD", "Your current password is incorrect.")
        user.password_hash = hash_password(body.new_password)
        await auth_service.revoke_all(session, user.id)
        await session.commit()

    # ── Admin ────────────────────────────────────────────────────────────

    async def _issue_invitation(self, session: AsyncSession, user: UserModel) -> Invitation:
        days = (await parameter_service.get(session)).invitation_expiry_days
        token = await auth_service.issue_token(
            session, user, TokenPurpose.INVITATION, timedelta(days=days)
        )
        return Invitation(user, token, days)

    async def invite(
        self,
        session: AsyncSession,
        *,
        email: str,
        first_name: str,
        last_name: str,
        mobile: str | None,
        role: RoleCode,
        venue_id: int,
    ) -> Invitation:
        """Create a staff account with NO password and email an invitation.

        role and venue_id are trusted here; the CONTROLLER decides where they
        come from — the manager route fixes both from the token.
        """
        if await session.get(VenueModel, venue_id) is None:
            raise AppError(422, "UNKNOWN_VENUE", "That venue does not exist.")
        user = await insert_user(
            session,
            UserModel(
                email=email,
                password_hash=None,
                first_name=first_name,
                last_name=last_name,
                mobile=mobile,
                role_id=await user_repository.role_id(session, role),
                venue_id=venue_id,
                is_active=False,
            ),
        )
        # Mirror the role into its group until 0007 retires roles (ADR 0003).
        kind = GroupKind.MASTER if role == RoleCode.VENUE_MANAGER else GroupKind.STAFF
        await group_repository.add_member(
            session,
            await group_repository.venue_group_id(session, venue_id, kind),
            user.id,
            added_by=None,
        )
        invitation = await self._issue_invitation(session, user)
        await session.commit()
        return invitation

    async def get_for_admin(
        self, session: AsyncSession, user_id: int, *, scope: VenueScope
    ) -> UserModel:
        """Ownership after fetch: out of scope is 404, never 403 — a 403
        would confirm the user exists at another venue (spec §6.2)."""
        user = await user_repository.get_scoped(session, user_id, scope=scope)
        if user is None:
            raise not_found()
        return user

    async def _modifiable(
        self, session: AsyncSession, caller: UserModel, user_id: int, scope: VenueScope
    ) -> UserModel:
        target = await self.get_for_admin(session, user_id, scope=scope)
        if target.id == caller.id:
            # Your own account goes through /users/me; this route could
            # otherwise deactivate the last manager of a venue by accident.
            raise AppError(403, "FORBIDDEN", "Use your own profile settings for your account.")
        if not caller.system_admin and target.role.code != RoleCode.VENUE_STAFF:
            # A manager's USER:Manage covers VENUE_STAFF only (spec §6.3).
            raise AppError(403, "FORBIDDEN", "You may only manage staff accounts.")
        return target

    async def update_by_admin(
        self,
        session: AsyncSession,
        caller: UserModel,
        user_id: int,
        body: AdminUserUpdate,
        *,
        scope: VenueScope,
    ) -> UserModel:
        target = await self._modifiable(session, caller, user_id, scope)
        changes = body.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(target, field, value)
        if changes.get("is_active") is False:
            # Access tokens already die on the next request (the user is
            # loaded each time); this ends the 7-day refresh window too.
            await auth_service.revoke_all(session, target.id)
            # And every outstanding emailed link: accepting an invitation or
            # verifying an email sets is_active = True, so a live link would
            # let the user undo their own deactivation.
            await session.execute(
                delete(UserTokenModel).where(
                    UserTokenModel.user_id == target.id, UserTokenModel.used_at.is_(None)
                )
            )
        await session.commit()
        await session.refresh(target, ["role"])
        return target

    async def _pending_invitee(
        self, session: AsyncSession, caller: UserModel, user_id: int, scope: VenueScope
    ) -> UserModel:
        target = await self._modifiable(session, caller, user_id, scope)
        await session.refresh(target, ["password_hash"])
        if target.password_hash is not None:
            raise already_accepted()
        return target

    async def resend_invitation(
        self, session: AsyncSession, caller: UserModel, user_id: int, *, scope: VenueScope
    ) -> Invitation:
        target = await self._pending_invitee(session, caller, user_id, scope)
        invitation = await self._issue_invitation(session, target)  # kills the old link
        await session.commit()
        return invitation

    async def revoke_invitation(
        self, session: AsyncSession, caller: UserModel, user_id: int, *, scope: VenueScope
    ) -> None:
        target = await self._pending_invitee(session, caller, user_id, scope)
        await session.execute(
            delete(UserTokenModel).where(
                UserTokenModel.user_id == target.id,
                UserTokenModel.purpose == TokenPurpose.INVITATION,
                UserTokenModel.used_at.is_(None),
            )
        )
        await session.commit()


user_service = UserService()
