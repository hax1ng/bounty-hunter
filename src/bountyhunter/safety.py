"""Hard safety gates. Authorized work only. Reports never auto-submit."""

from __future__ import annotations

from bountyhunter.models import AuthorizationError, SurfaceSlice, Target

ATTESTATION = (
    "I have authorization to test this target (active bug bounty program or "
    "written permission). I will stay inside the in-scope assets I listed."
)

HUNT_BLOCKED_UNAUTH = "Cannot start a hunt: this Target is not marked authorized."
HUNT_BLOCKED_EMPTY_SCOPE = "Cannot start a hunt: add at least one in-scope asset."
HUNT_BLOCKED_SCOPE_CONFLICT = (
    "Cannot start a hunt: the same asset is listed as both in scope and out of scope."
)
CREATE_BLOCKED_UNAUTH = "Authorization checkbox is required to create a Target."
REPORTER_NO_SUBMIT = "Reporter never auto-submits. Export the write-up and submit it yourself."


def require_authorized_to_create(authorized: bool) -> None:
    if not authorized:
        raise AuthorizationError(CREATE_BLOCKED_UNAUTH)


def require_authorized_to_hunt(target: Target | None) -> None:
    if target is None:
        raise AuthorizationError("Open or create a Target first.")
    if not target.authorized:
        raise AuthorizationError(HUNT_BLOCKED_UNAUTH)


class ScopeGuard:
    """Gate every queued unit of work before an agent sees it."""

    @staticmethod
    def require_huntable(target: Target | None) -> Target:
        require_authorized_to_hunt(target)
        assert target is not None
        if not target.in_scope:
            raise AuthorizationError(HUNT_BLOCKED_EMPTY_SCOPE)
        conflicts = sorted(set(target.in_scope) & set(target.out_of_scope))
        if conflicts:
            raise AuthorizationError(
                f"{HUNT_BLOCKED_SCOPE_CONFLICT} Conflicts: {', '.join(conflicts)}"
            )
        return target

    @staticmethod
    def require_slice(target: Target, surface: SurfaceSlice) -> None:
        ScopeGuard.require_huntable(target)
        if surface.out_of_scope_overlap:
            overlap = ", ".join(surface.out_of_scope_overlap)
            raise AuthorizationError(f"Slice {surface.id} overlaps out-of-scope assets: {overlap}")
        if surface.asset not in target.in_scope:
            raise AuthorizationError(
                f"Slice {surface.id} is not bound to a listed in-scope asset."
            )


def reporter_submit_blocked() -> AuthorizationError:
    return AuthorizationError(REPORTER_NO_SUBMIT)
