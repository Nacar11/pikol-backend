from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.persistence.models import TokenPurpose, UserTokenModel
from src.auth.scope import VenueScope
from src.auth.security import hash_password, verify_password
from src.auth.service import auth_service, insert_user
from src.parameters.service import parameter_service
from src.users.domain.groups import GroupKind
from src.users.dto import AdminUserUpdate, PasswordChangeRequest, ProfileUpdate, UserOut
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserGroupModel, UserModel
from src.users.persistence.repository import user_repository
from src.utils.errors import AppError


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

    async def invitable_group(
        self, session: AsyncSession, caller: UserModel, group_id: int, *, scope: VenueScope
    ) -> UserGroupModel:
        """The group an invite may target. A group the caller cannot see is
        the same 422 as one that does not exist; a master group is the
        owner's alone; Customer is not a staff invitation at all."""
        group = await group_repository.get_scoped(session, group_id, scope=scope)
        if group is None:
            raise AppError(422, "UNKNOWN_GROUP", "That group does not exist.")
        if group.kind == GroupKind.CUSTOMER:
            raise AppError(422, "NOT_A_STAFF_GROUP", "Invitations are for staff groups.")
        if group.kind == GroupKind.MASTER and not caller.system_admin:
            raise AppError(403, "FORBIDDEN", "Only the owner can add venue masters.")
        return group

    async def invite(
        self,
        session: AsyncSession,
        caller: UserModel,
        *,
        email: str,
        first_name: str,
        last_name: str,
        mobile: str | None,
        group: UserGroupModel,
    ) -> Invitation:
        """Create a staff account with NO password, in `group`, and email an
        invitation. `group` comes from invitable_group; nothing here reads a
        venue from the request.

        An existing account is refused rather than joined: adding people who
        already have one is the members API, which never reveals more than
        an exact email match.
        """
        try:
            user = await insert_user(
                session,
                UserModel(
                    email=email,
                    password_hash=None,
                    first_name=first_name,
                    last_name=last_name,
                    mobile=mobile,
                    is_active=False,
                ),
            )
        except AppError as exc:
            if exc.code != "EMAIL_TAKEN":
                raise
            raise AppError(
                409, "EMAIL_EXISTS", "Already registered — add them from Venue staff."
            ) from None
        await group_repository.add_member(session, group.id, user.id, added_by=caller.id)
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
        if caller.system_admin:
            return target
        # Not the owner: scope is exactly one venue (get_venue_access).
        assert scope.venue_id is not None
        if await group_repository.is_master_at(session, target.id, scope.venue_id):
            raise AppError(403, "FORBIDDEN", "Only the owner can change a venue master.")
        if await group_repository.belongs_elsewhere(session, target.id, scope.venue_id):
            # Their account is not one venue's to change; leaving your groups is.
            raise AppError(
                403,
                "FORBIDDEN",
                "This person is also a customer or works at another venue. "
                "Remove them from your groups instead.",
            )
        return target

    async def out(self, session: AsyncSession, user: UserModel, *, scope: VenueScope) -> UserOut:
        groups = await group_repository.groups_for(session, [user.id], scope=scope)
        return UserOut.of(user, groups[user.id])

    async def out_many(
        self, session: AsyncSession, users: list[UserModel], *, scope: VenueScope
    ) -> list[UserOut]:
        groups = await group_repository.groups_for(session, [u.id for u in users], scope=scope)
        return [UserOut.of(u, groups[u.id]) for u in users]

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
