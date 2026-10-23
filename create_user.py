import uuid

from src.models.database import SessionLocal
from src.models.tenant import Tenant
from src.models.user import User
from src.models.role import Role, UserRole
from src.models.user import User, hash_password


EMAIL = "m.driyaskhan55@gmail.com"
PASSWORD = "Riyas123@"
ROLE_NAME = "SystemAdmin"


def create_user():
    db = SessionLocal()

    try:
        # Get existing tenant
        tenant = db.query(Tenant).first()

        # Create a tenant if database has none
        if not tenant:
            tenant = Tenant(name="Default Tenant")
            db.add(tenant)
            db.commit()
            db.refresh(tenant)

            print(f"Created tenant: {tenant.name}")

        # Get Admin role
        role = db.query(Role).filter(Role.name == ROLE_NAME).first()

        if not role:
            role = Role(
                role_id=uuid.uuid4(),
                name=ROLE_NAME,
                description="Administrator role",
            )
            db.add(role)
            db.commit()
            db.refresh(role)

            print(f"Created role: {ROLE_NAME}")

        # Check whether user already exists
        existing_user = (
            db.query(User)
            .filter(User.email == EMAIL)
            .first()
        )

        if existing_user:
            user = existing_user
            print(f"User already exists: {EMAIL}")
        else:
            user = User(
                user_id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
            )

            db.add(user)
            db.commit()
            db.refresh(user)

            print("User created successfully.")

        # Check whether role is already assigned
        existing_user_role = (
            db.query(UserRole)
            .filter(
                UserRole.user_id == user.user_id,
                UserRole.role_id == role.role_id,
                UserRole.tenant_id == tenant.tenant_id,
            )
            .first()
        )

        if not existing_user_role:
            user_role = UserRole(
                user_id=user.user_id,
                role_id=role.role_id,
                tenant_id=tenant.tenant_id,
            )

            db.add(user_role)
            db.commit()

            print(f"Assigned role: {ROLE_NAME}")
        else:
            print(f"Role already assigned: {ROLE_NAME}")

        print()
        print("User details:")
        print(f"Email: {EMAIL}")
        print(f"Password: {PASSWORD}")
        print(f"User ID: {user.user_id}")
        print(f"Tenant ID: {tenant.tenant_id}")
        print(f"Role: {ROLE_NAME}")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    create_user()