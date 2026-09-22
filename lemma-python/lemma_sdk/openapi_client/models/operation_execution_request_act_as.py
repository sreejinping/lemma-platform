from enum import Enum


class OperationExecutionRequestActAs(str, Enum):
    APP = "app"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
