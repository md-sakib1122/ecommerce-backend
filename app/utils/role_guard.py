# app/utils/role_guard.py
from fastapi import Depends, HTTPException, status, Request
from app.auth.jwt_handler import verify_token
ACCESS_COOKIE_NAME = "access_token"

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





async def sign_out_user(response: Response):
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        path="/",
        samesite="lax"
    )
    return {"message": "Sign out successful"}