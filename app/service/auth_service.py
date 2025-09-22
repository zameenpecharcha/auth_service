import grpc
import bcrypt
import jwt
import random
from app.utils.token_blacklist import (
    store_blacklisted_session_id,
    store_blacklisted_refresh_jti,
    is_token_blacklisted
)
from concurrent import futures
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.entity.user_entity import SessionLocal, User
import app.proto_files.auth_pb2 as auth_pb2
import app.proto_files.auth_pb2_grpc as auth_pb2_grpc
from app.utils.otp_utils import send_otp_email, send_otp_sms
from app.utils.redis_utils import store_otp, get_otp, delete_otp
from app.utils.log_utils import log_msg

from app.utils.jwt_utils import generate_tokens, decode_token, verify_jwt_token


def create_user_info(user):
    """Helper function to create UserInfo message from User model"""
    return auth_pb2.UserInfo(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        phone=user.phone if user.phone else "",
        profile_photo=user.profile_photo if user.profile_photo else "",
        role=user.role if user.role else "",
        address=user.address if user.address else "",
        latitude=user.latitude if user.latitude else 0.0,
        longitude=user.longitude if user.longitude else 0.0,
        bio=user.bio if user.bio else "",
        isactive=user.isactive,
        email_verified=user.email_verified,
        phone_verified=user.phone_verified,
        last_login_at=str(user.last_login_at) if user.last_login_at else "",
        created_at=str(user.created_at) if user.created_at else ""
    )

class AuthService(auth_pb2_grpc.AuthServiceServicer):

    def Login(self, request, context):
        correlation_id = context.peer()
        db: Session = SessionLocal()
        try:
            print(f"Login attempt for email: {request.email}")
            user = db.query(User).filter(User.email == request.email).first()
            
            if not user:
                print(f"User not found: {request.email}")
                log_msg("warning", "User not found", user_id=request.email, correlation_id=correlation_id)
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.LoginResponse()

            if not user.isactive:
                print(f"Inactive user attempted login: {request.email}")
                log_msg("warning", "Inactive user attempted login", user_id=request.email, correlation_id=correlation_id)
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("Account is inactive")
                return auth_pb2.LoginResponse()
                
            try:
                password_matches = bcrypt.checkpw(request.password.encode(), user.password.encode())
            except Exception as e:
                print(f"Password verification error: {str(e)}")
                password_matches = False
                
            if not password_matches:
                print(f"Invalid password for user: {request.email}")
                log_msg("warning", "Invalid password", user_id=request.email, correlation_id=correlation_id)
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid credentials")
                return auth_pb2.LoginResponse()

            # Update last login time
            user.last_login_at = datetime.utcnow()
            db.commit()
            access_token, refresh_token = generate_tokens(user)
            print(f"Login successful for user: {request.email}")
            log_msg("info", "User logged in successfully", user_id=request.email, correlation_id=correlation_id)
            
            return auth_pb2.LoginResponse(
                token=access_token,
                refresh_token=refresh_token,
                user_info=create_user_info(user)
            )
        except Exception as e:
            print(f"Login error: {str(e)}")
            log_msg("error", f"Login error: {str(e)}", user_id=request.email, correlation_id=correlation_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal server error: {str(e)}")
            return auth_pb2.LoginResponse()
        finally:
            db.close()

    def Logout(self, request, context):
        correlation_id = context.peer()
        try:
            print("Logout attempt")

            if request.token:
                try:
                    access_payload = decode_token(request.token)
                    session_id = access_payload.get("session_id")
                    if session_id:
                        store_blacklisted_session_id(session_id)
                        print(f"Blacklisted session_id: {session_id}")
                except Exception as e:
                    print(f"Access token decode error (ignored): {e}")

            if request.refresh_token:
                try:
                    refresh_payload = decode_token(request.refresh_token)
                    refresh_jti = refresh_payload.get("jti")
                    if refresh_jti:
                        store_blacklisted_refresh_jti(refresh_jti)
                        print(f"Blacklisted refresh jti: {refresh_jti}")
                except Exception as e:
                    print(f"Refresh token decode error (ignored): {e}")

            return auth_pb2.LogoutResponse(
                success=True,
                message="Logged out successfully"
            )

        except Exception as e:
            print(f"Logout error: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal server error: {str(e)}")
            return auth_pb2.LogoutResponse(success=False, message=str(e))

    def ValidateToken(self, request, context):
        correlation_id = context.peer()
        db: Session = SessionLocal()
        try:
            print(f"Token validation attempt")
            
            # Verify token
            payload, error = verify_jwt_token(request.token)
            if error:
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details(error)
                return auth_pb2.ValidateTokenResponse(valid=False, message=error)
            
            # Get user from database
            user = db.query(User).filter(User.email == payload['email']).first()
            if not user:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.ValidateTokenResponse(valid=False, message="User not found")
            
            if not user.isactive:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("User account is inactive")
                return auth_pb2.ValidateTokenResponse(valid=False, message="User account is inactive")
            
            log_msg("info", "Token validated successfully", user_id=user.email, correlation_id=correlation_id)
            return auth_pb2.ValidateTokenResponse(
                valid=True,
                user_info=create_user_info(user),
                message="Token is valid"
            )
        except Exception as e:
            print(f"Token validation error: {str(e)}")
            log_msg("error", f"Token validation error: {str(e)}", correlation_id=correlation_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal server error: {str(e)}")
            return auth_pb2.ValidateTokenResponse(valid=False, message=str(e))
        finally:
            db.close()

    def SendOTP(self, request, context):
        correlation_id = context.peer()
        db: Session = SessionLocal()
        try:
            # Check if user exists and is active
            user = db.query(User).filter(User.email == request.email).first()
            if not user:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.OTPResponse(success=False, message="User not found")

            if not user.isactive:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("Account is inactive")
                return auth_pb2.OTPResponse(success=False, message="Account is inactive")

            # Validate OTP type
            if request.type not in [auth_pb2.VERIFICATION, auth_pb2.PASSWORD_RESET, auth_pb2.LOGIN]:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Invalid OTP type")
                return auth_pb2.OTPResponse(success=False, message="Invalid OTP type")

            # For verification type, check if already verified
            if request.type == auth_pb2.VERIFICATION:
                if user.email_verified:
                    context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                    context.set_details("Email already verified")
                    return auth_pb2.OTPResponse(success=False, message="Email already verified")

            # Generate OTP
            otp_code = str(random.randint(100000, 999999))
            channels = []

            # Store OTP with type
            store_key = f"{request.email}:{request.type}"
            print(f"Storing OTP with key: {store_key}")  # Debug log
            store_success = store_otp(store_key, otp_code)
            if not store_success:
                raise Exception("Failed to store OTP")

            # Send via email
            try:
                # Customize message based on OTP type
                purpose = {
                    auth_pb2.VERIFICATION: "email verification",
                    auth_pb2.PASSWORD_RESET: "password reset",
                    auth_pb2.LOGIN: "login"
                }.get(request.type, "verification")
                
                send_otp_email(request.email, otp_code, purpose)
                channels.append("email")
            except Exception as e:
                print(f"Failed to send email OTP: {str(e)}")

            # Send via SMS if phone is provided and verified
            if request.phone and user.phone_verified:
                try:
                    send_otp_sms(request.phone, otp_code)
                    channels.append("sms")
                except Exception as e:
                    print(f"Failed to send SMS OTP: {str(e)}")

            if not channels:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details("Failed to send OTP through any channel")
                return auth_pb2.OTPResponse(success=False, message="Failed to send OTP")

            log_msg("info", f"OTP sent successfully via {', '.join(channels)}", user_id=request.email, correlation_id=correlation_id)
            return auth_pb2.OTPResponse(
                success=True,
                message=f"OTP sent successfully via {', '.join(channels)}",
                channels=channels
            )
        except Exception as e:
            print(f"SendOTP error: {str(e)}")
            log_msg("error", f"SendOTP error: {str(e)}", user_id=request.email, correlation_id=correlation_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Failed to send OTP")
            return auth_pb2.OTPResponse(success=False, message=str(e))
        finally:
            db.close()

    def VerifyOTP(self, request, context):
        correlation_id = context.peer()
        db: Session = SessionLocal()
        try:
            # Check if user exists and is active
            user = db.query(User).filter(User.email == request.email).first()
            if not user:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.VerifyOTPResponse(success=False, message="User not found")

            if not user.isactive:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("Account is inactive")
                return auth_pb2.VerifyOTPResponse(success=False, message="Account is inactive")

            # Validate OTP type
            if request.type not in [auth_pb2.VERIFICATION, auth_pb2.PASSWORD_RESET, auth_pb2.LOGIN]:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Invalid OTP type")
                return auth_pb2.VerifyOTPResponse(success=False, message="Invalid OTP type")

            # Get stored OTP with type
            store_key = f"{request.email}:{request.type}"
            print(f"Retrieving OTP with key: {store_key}")  # Debug log
            stored_otp = get_otp(store_key)
            
            if not stored_otp:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("OTP expired or not found")
                return auth_pb2.VerifyOTPResponse(success=False, message="OTP expired or not found")

            if stored_otp != request.otp_code:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Invalid OTP")
                return auth_pb2.VerifyOTPResponse(success=False, message="Invalid OTP")

            # Delete used OTP only for VERIFICATION and LOGIN types
            if request.type != auth_pb2.PASSWORD_RESET:
                delete_otp(store_key)

            # Handle specific OTP type actions
            if request.type == auth_pb2.VERIFICATION:
                # Mark email as verified
                user.email_verified = True
                db.commit()
                message = "Email verified successfully"
            elif request.type == auth_pb2.PASSWORD_RESET:
                message = "OTP verified, proceed with password reset"
            else:  # LOGIN
                message = "OTP verified successfully"

            # Generate token with user information
            token_data = {
                "user_id": user.id,
                "email": user.email,
                "role": user.role,
                "exp": datetime.utcnow() + timedelta(hours=1)
            }
            token = jwt.encode(token_data, SECRET_KEY, algorithm="HS256")

            log_msg("info", message, user_id=request.email, correlation_id=correlation_id)
            return auth_pb2.VerifyOTPResponse(
                success=True,
                message=message,
                token=token,
                user_info=create_user_info(user)
            )
        except Exception as e:
            print(f"VerifyOTP error: {str(e)}")
            log_msg("error", f"VerifyOTP error: {str(e)}", user_id=request.email, correlation_id=correlation_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Error verifying OTP: {str(e)}")
            return auth_pb2.VerifyOTPResponse(success=False, message=str(e))
        finally:
            db.close()

    def ForgotPassword(self, request, context):
        correlation_id = context.peer()
        db: Session = SessionLocal()
        try:
            # Check if user exists and is active
            user = db.query(User).filter(User.email == request.email).first()
            if not user:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.ForgotPasswordResponse(success=False, message="User not found")

            if not user.isactive:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("Account is inactive")
                return auth_pb2.ForgotPasswordResponse(success=False, message="Account is inactive")

            # Generate and store OTP
            otp_code = str(random.randint(100000, 999999))
            store_key = f"{request.email}:1"  # Use numeric type for consistency
            print(f"Storing OTP with key: {store_key}")  # Debug log
            channels = []

            store_success = store_otp(store_key, otp_code)
            if not store_success:
                raise Exception("Failed to store OTP")

            # Send via email
            try:
                send_otp_email(request.email, otp_code, "password reset")
                channels.append("email")
            except Exception as e:
                print(f"Failed to send email OTP: {str(e)}")

            # Send via SMS if phone is provided and verified
            if request.phone and user.phone_verified:
                try:
                    send_otp_sms(request.phone, otp_code)
                    channels.append("sms")
                except Exception as e:
                    print(f"Failed to send SMS OTP: {str(e)}")

            if not channels:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details("Failed to send reset instructions")
                return auth_pb2.ForgotPasswordResponse(success=False, message="Failed to send reset instructions")

            log_msg("info", "Password reset OTP sent", user_id=request.email, correlation_id=correlation_id)
            return auth_pb2.ForgotPasswordResponse(
                success=True,
                message=f"Reset instructions sent via {', '.join(channels)}",
                channels=channels
            )
        except Exception as e:
            print(f"ForgotPassword error: {str(e)}")
            log_msg("error", f"ForgotPassword error: {str(e)}", user_id=request.email, correlation_id=correlation_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Failed to process password reset request")
            return auth_pb2.ForgotPasswordResponse(success=False, message=str(e))
        finally:
            db.close()

    def ResetPassword(self, request, context):
        correlation_id = context.peer()
        db: Session = SessionLocal()
        try:
            # Check if user exists and is active
            user = db.query(User).filter(User.email == request.email).first()
            if not user:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("User not found")
                return auth_pb2.ResetPasswordResponse(success=False, message="User not found")

            if not user.isactive:
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                context.set_details("Account is inactive")
                return auth_pb2.ResetPasswordResponse(success=False, message="Account is inactive")

            # Verify OTP
            store_key = f"{request.email}:1"  # Use numeric type for consistency
            print(f"Retrieving OTP with key: {store_key}")  # Debug log
            stored_otp = get_otp(store_key)
            if not stored_otp or stored_otp != request.otp_code:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Invalid or expired OTP")
                return auth_pb2.ResetPasswordResponse(success=False, message="Invalid or expired OTP")

            # Verify passwords match
            if request.new_password != request.confirm_password:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Passwords do not match")
                return auth_pb2.ResetPasswordResponse(success=False, message="Passwords do not match")

            # Hash new password
            hashed_password = bcrypt.hashpw(request.new_password.encode(), bcrypt.gensalt()).decode('utf-8')
            
            # Update password
            user.password = hashed_password
            db.commit()

            # Delete used OTP
            delete_otp(store_key)

            log_msg("info", "Password reset successful", user_id=request.email, correlation_id=correlation_id)
            return auth_pb2.ResetPasswordResponse(
                success=True,
                message="Password reset successful",
                user_info=create_user_info(user)
            )
        except Exception as e:
            print(f"ResetPassword error: {str(e)}")
            log_msg("error", f"ResetPassword error: {str(e)}", user_id=request.email, correlation_id=correlation_id)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Failed to reset password")
            return auth_pb2.ResetPasswordResponse(success=False, message=str(e))
        finally:
            db.close()

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    auth_pb2_grpc.add_AuthServiceServicer_to_server(AuthService(), server)
    server.add_insecure_port('localhost:50052')
    print("Starting auth service on port 50052...")
    server.start()
    server.wait_for_termination()
