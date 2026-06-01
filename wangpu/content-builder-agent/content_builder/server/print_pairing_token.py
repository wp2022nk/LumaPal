"""Print the local phone pairing token."""

from .security import pairing_token


if __name__ == "__main__":
    print(pairing_token())

