from sqlalchemy import Column, Integer, String
from config.database import Base

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    sourceFile = Column(String(255))
    referenceFile = Column(String(255))
    translatedFile = Column(String(255), nullable=True)
    progress = Column(Integer, default=0)