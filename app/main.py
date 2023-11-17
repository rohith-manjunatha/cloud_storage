# main.py

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
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

def authenticate_user(username, password):
    # connection = pymysql.connect(host=RDSConfig.ENDPOINT,
    #                              user=RDSConfig.USERNAME,
    #                              password=RDSConfig.PASSWORD,
    #                              database=RDSConfig.DATABASE_NAME,
    #                              charset='utf8mb4',
    #                              cursorclass=pymysql.cursors.DictCursor)
    config = {
  'user': 'root',
  'password': 'root',
  'host': 'localhost',
  'port': 3306,
  'database': 'cloud_storage'
}
    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cursor:
            # Check user credentials in the database
            cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
            user = cursor.fetchone()
            return user is not None
    finally:
        connection.close()

def get_s3_client():
    return boto3.client('s3', aws_access_key_id=AWSConfig.ACCESS_KEY,
                       aws_secret_access_key=AWSConfig.SECRET_KEY)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Example: Basic user authentication (replace with a more secure method in production)
    if authenticate_user(username, password):
        # Set session data (example: storing the username)
        request.session["username"] = username
        return RedirectResponse(url="/dashboard", status_code=302)
    else:
        return {"message": "Invalid credentials"}

@app.get("/dashboard", response_class=HTMLResponse)
async def read_dashboard(request: Request, deleted: bool = False):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/", status_code=302)

    # Get the username from the session
    username = request.session["username"]

    # Retrieve a list of files from your S3 bucket (replace with actual logic)
    s3 = get_s3_client()
    files = [obj['Key'] for obj in s3.list_objects(Bucket=AWSConfig.S3_BUCKET_NAME).get('Contents', [])]

    return templates.TemplateResponse("dashboard.html", {"request": request, "username": username, "files": files, "deleted": deleted})

@app.post("/uploadfile/", response_class=HTMLResponse)
async def create_upload_file(request: Request, file: UploadFile = File(...)):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/", status_code=302)

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
        return RedirectResponse(url="/", status_code=302)

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

def delete_file_safely(filename, s3):
    # Perform additional safety checks or logging if needed before deleting
    # For now, just delete the file from Amazon S3
    s3.delete_object(Bucket=AWSConfig.S3_BUCKET_NAME, Key=filename)

@app.get("/deletefile/{filename}", response_class=HTMLResponse)
async def delete_file(filename: str, request: Request):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/", status_code=302)

    # Render a confirmation page with the filename
    return templates.TemplateResponse("delete_confirmation.html", {"request": request, "filename": filename})

@app.post("/confirmdelete/{filename}", response_class=HTMLResponse)
async def confirm_delete_file(filename: str, request: Request):
    # Check if the user is authenticated
    if "username" not in request.session:
        return RedirectResponse(url="/", status_code=302)

    # Perform the actual file deletion
    try:
        s3 = get_s3_client()
        delete_file_safely(filename, s3)
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")

    # Redirect back to the dashboard with a success message
    return RedirectResponse(url="/dashboard?deleted=true", status_code=302)