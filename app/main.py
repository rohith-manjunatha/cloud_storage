# main.py

from fastapi import Depends, FastAPI, UploadFile, File, Form, Request, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from .config import AWSConfig, RDSConfig

import boto3
from botocore.exceptions import NoCredentialsError
import pymysql

app = FastAPI()

# Mount static and templates directories
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Example: SessionMiddleware for user authentication (use a more secure method in production)
app.add_middleware(SessionMiddleware, secret_key="secret_key")

config = {
  'user': RDSConfig.USERNAME,
  'password': RDSConfig.PASSWORD,
  'host': RDSConfig.ENDPOINT,
  'port': RDSConfig.PORT,
  'database': RDSConfig.DATABASE_NAME
}

connection = pymysql.connect(**config)

def authenticate_user(username, password):
    
    with connection.cursor() as cursor:
        # Check user credentials in the database
        cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = cursor.fetchone()
        return user is not None

def get_name_of_user(username):
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM users WHERE username=%s", (username))
        name = cursor.fetchone()
        return name

def get_s3_client():
    return boto3.client('s3', aws_access_key_id=AWSConfig.ACCESS_KEY,
                       aws_secret_access_key=AWSConfig.SECRET_KEY)

def delete_file_safely(filename, s3):
    # Perform additional safety checks or logging if needed before deleting
    # For now, just delete the file from Amazon S3
    s3.delete_object(Bucket=AWSConfig.S3_BUCKET_NAME, Key=filename)

# Dependency to get the current user based on the session
async def get_current_user(request: Request):
    user = request.session.get("username")
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, message: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "message": message})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Example: Basic user authentication (replace with a more secure method in production)
    if authenticate_user(username, password):
        # Set session data (example: storing the username)
        request.session["username"] = username
        return RedirectResponse(url="/dashboard", status_code=302)
    else:
        message = "Invalid credentials. Please try again."
        return RedirectResponse("/login?message=" + message, status_code=303)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")

@app.get("/share", response_class=HTMLResponse)
async def share_page(request: Request, message: str = None):
    return templates.TemplateResponse("share.html", {"request": request, "message": message})


@app.post("/share")
async def share(
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
):
    try:
        with connection.cursor() as cursor:
            # Check if the username or email already exists
            query = "SELECT * FROM users WHERE username = %s"
            cursor.execute(query, (username,))
            result_username = cursor.fetchone()

            query = "SELECT * FROM users WHERE email = %s"
            cursor.execute(query, (email,))
            result_email = cursor.fetchone()

            if result_username:
                message = "Username already exists. Please choose a different one."
                return RedirectResponse("/share?message=" + message, status_code=303)

            if result_email:
                message = "Email already exists. Please choose a different one."
                return RedirectResponse("/share?message=" + message, status_code=303)

            # Insert the new user into the database
            query = "INSERT INTO users (username, password, name, email) VALUES (%s, %s, %s, %s)"
            cursor.execute(query, (username, password, name, email))

        connection.commit()

        # Redirect to the login page on successful signup
        return RedirectResponse("/login")
    except Exception as e:
        message = f"An error occurred: {str(e)}"
        return RedirectResponse("/share?message=" + message, status_code=303)

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(get_current_user)])
async def dashboard(request: Request, deleted: bool = False):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    # Get the username from the session
    username = request.session["username"]
    # Retrieve a list of files from your S3 bucket (replace with actual logic)
    s3 = get_s3_client()
    files = [obj['Key'] for obj in s3.list_objects(Bucket=AWSConfig.S3_BUCKET_NAME).get('Contents', [])]

    return templates.TemplateResponse("dashboard.html", {"request": request, "name": get_name_of_user(username)[0].capitalize(), "files": files, "deleted": deleted})

@app.post("/uploadfile/", response_class=HTMLResponse)
async def create_upload_file(request: Request, file: UploadFile = File(...)):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    # Example: Save the uploaded file to Amazon S3
    try:
        s3 = get_s3_client()
        s3.upload_fileobj(file.file, AWSConfig.S3_BUCKET_NAME, file.filename)

        # Redirect to the success page
        return templates.TemplateResponse("upload_success.html", {"request": request, "filename": file.filename})
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")

@app.get("/downloadfile/{filename}", response_class=HTMLResponse)
async def download_file(filename: str, request: Request):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    # Generate a pre-signed URL for the S3 object
    try:
        s3 = get_s3_client()
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': AWSConfig.S3_BUCKET_NAME, 'Key': filename},
            ExpiresIn=3600  # URL expiration time in seconds (adjust as needed)
        )

        # Redirect to the success page
        return templates.TemplateResponse("download_success.html", {"request": request, "filename": filename, "download_url": url})
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")

@app.get("/deletefile/{filename}", response_class=HTMLResponse)
async def delete_file(filename: str, request: Request):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    # Render a confirmation page with the filename
    return templates.TemplateResponse("delete_confirmation.html", {"request": request, "filename": filename})

@app.post("/confirmdelete/{filename}", response_class=HTMLResponse)
async def confirm_delete_file(filename: str, request: Request):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    # Perform the actual file deletion
    try:
        s3 = get_s3_client()
        delete_file_safely(filename, s3)
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")

    # Redirect back to the dashboard with a success message
    return RedirectResponse(url="/dashboard?deleted=true", status_code=302)
