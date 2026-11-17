from uuid import uuid4

import pytest

from app.domain.access import Role, TripAccess, parse_trip_id
from app.domain.errors import Forbidden, NotFound


@pytest.mark.parametrize("role,required,allowed", [
    (Role.ORGANIZER, Role.ORGANIZER, True),
    (Role.ORGANIZER, Role.EDITOR, True),
    (Role.ORGANIZER, Role.VIEWER, True),
    (Role.EDITOR, Role.ORGANIZER, False),
    (Role.EDITOR, Role.EDITOR, True),
    (Role.EDITOR, Role.VIEWER, True),
    (Role.VIEWER, Role.ORGANIZER, False),
    (Role.VIEWER, Role.EDITOR, False),
    (Role.VIEWER, Role.VIEWER, True),
])
def test_roles_inherit_lower_capabilities(role, required, allowed):
    access = TripAccess(uuid4(), "u", role)
    if allowed:
        access.require(required)
    else:
        with pytest.raises(Forbidden):
            access.require(required)


def test_malformed_trip_id_is_reported_as_not_found():
    with pytest.raises(NotFound) as exc:
        parse_trip_id("not-a-uuid")
    assert exc.value.code == "TRIP_NOT_FOUND"
