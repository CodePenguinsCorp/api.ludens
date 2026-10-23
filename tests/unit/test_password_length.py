import pytest

from app.core.domain import DomainError
from app.modules.identity.infrastructure.services import PasswordService


@pytest.mark.parametrize("password", ["a" * 73, "é" * 37])
def test_passwords_above_bcrypt_byte_limit_return_a_business_error(password):
    with pytest.raises(DomainError, match="senha é muito longa"):
        PasswordService().hash(password)


@pytest.mark.parametrize("password", ["a" * 72, "é" * 36])
def test_passwords_at_the_byte_limit_roundtrip(password):
    service = PasswordService()
    assert service.verify(password, service.hash(password))
