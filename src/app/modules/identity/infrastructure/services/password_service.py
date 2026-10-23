import bcrypt
from app.core.domain import DomainError

class PasswordService:
    def hash(self, plain: str) -> str:
        encoded = plain.encode("utf-8")
        if len(encoded) > 72:
            raise DomainError("A senha é muito longa. Use menos caracteres.")
        return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")

    def verify(self, plain: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except ValueError:
            return False
