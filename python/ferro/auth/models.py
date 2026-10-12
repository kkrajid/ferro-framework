"""Built-in authentication models: ``User``, ``Group``, ``Permission`` and ``Session``.

They are ordinary Ferro models (tables ``auth_user``, ``auth_group``, ``auth_permission``,
``auth_session`` plus link tables), so ``ferro makemigrations`` includes them once
``ferro.auth`` is imported.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ferro import models
from ferro.auth.hashers import make_password, validate_password
from ferro.models import Q
from ferro.models.queryset import QuerySet

__all__ = ["AnonymousUser", "Group", "Permission", "Session", "User"]


def _now() -> datetime:
    return datetime.now(UTC)


class Permission(models.Model):
    """A named permission, e.g. ``add_product`` (``<action>_<table>``)."""

    codename = models.String(max_length=100, unique=True)
    name = models.String(max_length=255, default="")

    class Meta:
        table = "auth_permission"

    def __str__(self) -> str:
        return self.codename


class Group(models.Model):
    name = models.String(max_length=150, unique=True)
    permissions = models.ManyToMany(Permission, related_name="groups")

    class Meta:
        table = "auth_group"

    def __str__(self) -> str:
        return self.name


class User(models.Model):
    """A user account. Passwords are stored only as Argon2id hashes."""

    username = models.String(max_length=150, unique=True)
    email = models.String(max_length=254, default="")
    password = models.String(max_length=255, default="")
    is_active = models.Boolean(default=True)
    is_staff = models.Boolean(default=False)
    is_superuser = models.Boolean(default=False)
    date_joined = models.DateTime(default=_now)
    last_login = models.DateTime(null=True)
    groups = models.ManyToMany(Group, related_name="users")
    user_permissions = models.ManyToMany(Permission, related_name="users")

    class Meta:
        table = "auth_user"

    is_authenticated = True
    is_anonymous = False

    async def set_password(self, password: str) -> None:
        """Validates and hashes ``password`` (call ``save()`` afterwards)."""
        validate_password(password, self.username)
        self.password = await make_password(password)

    async def check_password(self, password: str) -> bool:
        from ferro.auth.hashers import check_password

        matches, _ = await check_password(password, self.password)
        return matches

    async def has_perm(self, codename: str) -> bool:
        """Whether the user holds ``codename`` directly or through a group. Active superusers
        hold every permission; inactive users none."""
        return await self.has_perms([codename])

    async def has_perms(self, codenames: list[str] | tuple[str, ...]) -> bool:
        wanted = set(codenames)
        if not self.is_active:
            return False
        if self.is_superuser or not wanted:
            return True
        cache: dict[str, bool] = self.__dict__.setdefault("_perm_cache", {})
        unknown = [code for code in wanted if code not in cache]
        if unknown:
            held = set(
                await Permission.objects.filter(
                    Q(users=self.pk) | Q(groups__users=self.pk), codename__in=unknown
                ).values_list("codename", flat=True)
            )
            for code in unknown:
                cache[code] = code in held
        return all(cache[code] for code in wanted)

    def to_dict(self) -> dict[str, Any]:
        """Fields without the password hash."""
        data = super().to_dict()
        data.pop("password", None)
        return data

    def __str__(self) -> str:
        return self.username


class UserQuerySet(QuerySet[User]):
    """``User.objects``: the usual queryset plus ``create_user`` / ``create_superuser``."""

    __slots__ = ()

    async def create_user(
        self, username: str, password: str | None = None, *, email: str = "", **fields: Any
    ) -> User:
        user = User(username=username.strip(), email=email.strip(), **fields)
        if password is not None:
            await user.set_password(password)
        await user.save()
        return user

    async def create_superuser(
        self, username: str, password: str, *, email: str = "", **fields: Any
    ) -> User:
        fields.update(is_staff=True, is_superuser=True, is_active=True)
        return await self.create_user(username, password, email=email, **fields)


User.objects = UserQuerySet(User)


class Session(models.Model):
    """A server-side session; only a SHA-256 digest of the browser's key is stored."""

    key_digest = models.String(max_length=64, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, related_name="+")
    data = models.JSON(default=dict)
    expires = models.DateTime(index=True)

    class Meta:
        table = "auth_session"


class AnonymousUser:
    """``request.user`` when nobody is logged in."""

    pk = None
    id = None
    username = ""
    email = ""
    is_active = False
    is_staff = False
    is_superuser = False
    is_authenticated = False
    is_anonymous = True

    async def has_perm(self, codename: str) -> bool:
        return False

    async def has_perms(self, codenames: list[str] | tuple[str, ...]) -> bool:
        return not codenames

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<AnonymousUser>"
