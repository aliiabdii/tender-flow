from sqlalchemy.orm import DeclarativeBase

# Advance with application migrations; queue migrations remain library-owned.
APPLICATION_SCHEMA_REVISION = "0003"


class Base(DeclarativeBase):
    pass
