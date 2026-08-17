# Medii AI Pipeline

Production AI backend for processing, translating, and validating regulated pharmaceutical and medical documentation.

Built as the core AI processing layer behind Medii.ai, a pharmaceutical workflow platform later acquired by Dor Services (Novolog Group).

## Overview

Medii AI Pipeline automates complex regulatory document workflows that traditionally require extensive manual translation, validation, and review.

The system combines document processing, LLM-based translation, automated quality validation, and human review into a single backend workflow.

## Architecture

```text
Document Input
      ↓
FastAPI API Layer
      ↓
Document Processing
      ↓
LLM Translation Pipeline
      ↓
Automated QA & Validation
      ↓
Human Review
      ↓
Validated Output
```

## Core Components

### Document Processing

Handles document ingestion, parsing, segmentation, and preparation for downstream AI processing.

### LLM Translation

Runs domain-specific pharmaceutical and medical translation workflows using LLM APIs and structured prompting.

### Quality Validation

Evaluates generated content for consistency, omissions, terminology issues, and other quality failures before human approval.

### Human-in-the-Loop

Routes AI-generated output through human review for workflows where accuracy and traceability are critical.

### API Layer

FastAPI services expose the processing pipeline and manage requests, validation, errors, and workflow state.

## Repository Structure

```text
app/
├── main.py              # FastAPI application and API endpoints
├── schemas.py           # Request and response models
└── models/              # Application data models

config/
├── database.py          # Database configuration
└── setting.py           # Application settings

translator/
├── translate.py         # LLM translation pipeline
├── qa_engine.py         # Automated quality validation
├── doc_manager.py       # Document processing
└── counter.py           # Token utilities

prompt/
└── template_3.txt       # Translation prompt template
```

## Tech Stack

- Python
- FastAPI
- Anthropic API
- SQLAlchemy
- MySQL
- Pydantic
- Async Python
- LLM-based validation

## Engineering Principles

- Human review for high-stakes AI outputs
- Explicit validation instead of relying on raw LLM responses
- Modular separation between API, document processing, translation, and QA
- Asynchronous processing for long-running AI workloads
- Environment-based configuration for credentials and deployment

## Local Setup

Clone the repository:

```bash
git clone https://github.com/aviramshab/medii-ai-pipeline.git
cd medii-ai-pipeline
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your local environment configuration:

```bash
cp .env.example .env
```

Add the required credentials to `.env`, then run:

```bash
python run.py
```

## Background

Medii.ai was built to automate complex pharmaceutical and regulatory documentation workflows and was later acquired by Dor Services, part of Novolog Group.

This repository contains the backend AI processing components of the system.