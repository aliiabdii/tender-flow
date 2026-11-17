from pydantic import BaseModel


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: str
