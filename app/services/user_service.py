# class UserService
# app/utils/role_guard.py
from fastapi import Depends, HTTPException, status, Request
from app.auth.jwt_handler import verify_token

from datetime import datetime
from app.core.databse import db
from app.auth.password_utils import hash_password

# JWT verification dependency
def get_current_user(request: Request):
    token = request.cookies.get("access_token")  # from HttpOnly cookie
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = verify_token(token)
    if not payload or "error" in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=payload.get("error", "Invalid token"))
    print("this is payload",payload)
    return payload

def require_role(roles: list[str]):
    def role_checker(user=Depends(get_current_user)):
        user_role = user.get("role")
        if user_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: requires one of {roles} roles"
            )
        return user
    return role_checker





async def create_user(data: dict):
    collection = db["users"]
    hashed_pw = hash_password(data["password"])
    print("create user data",data)
    document = {}

    if data["role"] in ["group", "company"]:
        document = {
            "name": data["name"],
            "email": data["email"],
            "password": hashed_pw,
            "role": data["role"],
            "is_verified":False,
            #company
            "parent": data.get("parent"),
            "department": [],
            "subdepartment": [],
            "company_code": data.get("company_code"),
            "address": data.get("address"),
            "city": data.get("city"),
            "fax":data.get("fax"),
            "phone": data.get("phone"),
            "tax_no": data.get("tax_no"),
            "country": data.get("country"),
            "abbreviate_name": data.get("abbreviate_name"),
            #company
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }

    elif data["role"] == "employee":
        document = {
            "name": data["name"],
            "email": data["email"],
            "password": hashed_pw,
            "is_verified": False,
            "role": data["role"],
            "company_id": data.get("company_id"),
            "emp_id": data.get("emp_id"),
            "group_id": data.get("group_id"),
            "userSubDept" :data.get("userSubDept"),
            "userDept":data.get("userDept"),
            "lon": data.get("lon"),
            "lat": data.get("lat"),
            "locations" : data.get("locations", []),
            "radius": data.get("radius"),
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }


    result = await collection.insert_one(document)

    return str(result.inserted_id)