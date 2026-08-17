from time import time
from fastapi import FastAPI, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from sqlalchemy.orm import Session
from typing import Dict, Any 
from fastapi import UploadFile, File
import uuid

# Use absolute imports instead of relative imports
from config.setting import settings
from config.database import get_db
from app.models.project import Project 
from app.schemas import TranslateResponse
from translator.translate import process_document_final, TranslationAPIError

app = FastAPI(title="Medii-AI Translator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.upload_directory, exist_ok=True)
os.makedirs(settings.output_directory, exist_ok=True)

@app.post("/translate", response_model=TranslateResponse)
async def translate(
    project_id: int = Form(...),
    db: Session = Depends(get_db)
):
    # 1. Load project
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # File paths
    main_path = os.path.join(settings.upload_directory, project.sourceFile)
    temp_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "uploads",
        "DOR-38 Patient leaflet-ENG.docx"
    )
    
    os.path.join(settings.upload_directory, 'DOR-38 Patient leaflet-ENG.docx')
    ref_path = (
        os.path.join(settings.upload_directory, project.referenceFile)
        if project.referenceFile else None
    )

    if not os.path.exists(main_path):
        raise HTTPException(status_code=404, detail="Source file not found")

    project.progress = 1
    db.commit()
    
    def update_progress(value: int):
          if isinstance(project.progress, int) and value > project.progress:
               project.progress = value
               db.commit()
               print(f"[Progress] Project {project_id}: {value}%")
    
    
    base_name = os.path.splitext(project.sourceFile)[0]
    output_filename = f"{base_name}_translated_{time()}.docx"
    
    output_path = os.path.join(settings.output_directory, output_filename)

    try:
        result: Dict[str, Any] = process_document_final(
            input_path=main_path,
            reference_path=ref_path,
            output_path=output_path,
            template_path=temp_path,   
            progress_callback=update_progress
        )

        project.progress = 100
        project.translatedFile = output_filename
        db.commit()

        total_tokens = result["total_input_tokens"] + result["total_output_tokens"]
        
        response_data = {
            "engine": settings.anthropic_model, 
            "file_name": output_filename,      
            "time_sec": result["total_time"],  
            "tokens": total_tokens,      
            "download_url": f"/download_output/{output_filename}"
        }
        
        return TranslateResponse(**response_data)

    except TranslationAPIError as e:
        # Handle API-specific errors with proper rollback
        db.rollback()
        project.progress = 0
        db.commit()

        # Map error types to appropriate HTTP status codes
        status_code_map = {
            "insufficient_credits": 402,  # Payment Required
            "authentication_error": 401,  # Unauthorized
            "rate_limit_error": 429,      # Too Many Requests
            "token_limit_error": 413,     # Payload Too Large
            "connection_error": 503,      # Service Unavailable
            "client_unavailable": 503,    # Service Unavailable
            "api_error": 502,             # Bad Gateway
            "unknown_error": 500          # Internal Server Error
        }
        
        status_code = status_code_map.get(e.error_type, 500)
        
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": e.error_type.upper(),
                "message": e.message
            }
        )

    except Exception as e:
        db.rollback()
        project.progress = 0
        db.commit()

        msg = str(e)

        if msg.startswith("TOKEN_LIMIT_EXCEEDED"):
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "TOKEN_LIMIT_EXCEEDED",
                    "message": msg.replace("TOKEN_LIMIT_EXCEEDED:", "").strip()
                }
            )

        raise HTTPException(
            status_code=500,
            detail=f"Translation error: {msg}"
        )


@app.get("/download_output/{filename}")
async def download_output(filename: str):

    file_path = os.path.join(settings.output_directory, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )



@app.post("/translate_direct_upload", response_model=TranslateResponse)
async def translate_direct_upload(
    source_file: UploadFile = File(...),
    reference_file: UploadFile =File(...),
    db: Session = Depends(get_db)
):
    """
    Direct translation without DB project.
    User uploads files manually. Template is fixed.
    """

    upload_dir = settings.upload_directory
    os.makedirs(upload_dir, exist_ok=True)

    #source
    source_ext = os.path.splitext(source_file.filename)[1]
    source_name = f"source_{uuid.uuid4().hex}{source_ext}"
    source_path = os.path.join(upload_dir, source_name)

    with open(source_path, "wb") as f:
        f.write(await source_file.read())

    ref_path = None
    if reference_file:
        ref_ext = os.path.splitext(reference_file.filename)[1]
        ref_name = f"ref_{uuid.uuid4().hex}{ref_ext}"
        ref_path = os.path.join(upload_dir, ref_name)
        with open(ref_path, "wb") as f:
            f.write(await reference_file.read())

    # OUTPUT 
    base_name = os.path.splitext(source_file.filename)[0]
    output_name = f"{base_name}_translated_{time()}.docx"
    output_path = os.path.join(settings.output_directory, output_name)

    # TEMPLATE (fixed)
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "uploads",
        "DOR-38 Patient leaflet-ENG.docx"
    )

    # Translate
    try:
        result = process_document_final(
            input_path=source_path,
            reference_path=ref_path,
            template_path=template_path,
            output_path=output_path,
            progress_callback=lambda x: None
        )

        total_tokens = result["total_input_tokens"] + result["total_output_tokens"]

        return TranslateResponse(
            engine=settings.anthropic_model,
            file_name=output_name,
            time_sec=result["total_time"],
            tokens=total_tokens,
            download_url=f"/download_output/{output_name}"
        )

    except TranslationAPIError as e:
        # Handle API-specific errors
        # Clean up uploaded files
        if os.path.exists(source_path):
            try:
                os.remove(source_path)
            except:
                pass
        if ref_path and os.path.exists(ref_path):
            try:
                os.remove(ref_path)
            except:
                pass
        
        # Map error types to appropriate HTTP status codes
        status_code_map = {
            "insufficient_credits": 402,  # Payment Required
            "authentication_error": 401,  # Unauthorized
            "rate_limit_error": 429,      # Too Many Requests
            "token_limit_error": 413,     # Payload Too Large
            "connection_error": 503,      # Service Unavailable
            "client_unavailable": 503,    # Service Unavailable
            "api_error": 502,             # Bad Gateway
            "unknown_error": 500          # Internal Server Error
        }
        
        status_code = status_code_map.get(e.error_type, 500)
        
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": e.error_type.upper(),
                "message": e.message
            }
        )
    
    except Exception as e:
        # Clean up uploaded files on any error
        if os.path.exists(source_path):
            try:
                os.remove(source_path)
            except:
                pass
        if ref_path and os.path.exists(ref_path):
            try:
                os.remove(ref_path)
            except:
                pass
        
        raise HTTPException(status_code=500, detail=f"Translation error: {str(e)}")