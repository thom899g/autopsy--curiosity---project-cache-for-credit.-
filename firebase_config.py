"""
Firebase Admin SDK configuration for Cache-for-Credit ledger.
Uses Firestore for: user credits, optimization reports, transaction history.
CRITICAL: State management MUST use Firebase per mission constraints.
"""
import os
import json
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

import firebase_admin
from firebase_admin import credentials, firestore, initialize_app
from firebase_admin.exceptions import FirebaseError
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger(__name__)

@dataclass
class CacheProfile:
    """Data model for cache profiling results"""
    process_id: int
    process_name: str
    memory_mb: float
    cpu_percent: float
    io_read_mb: float
    io_write_mb: float
    threads: int
    optimization_credits_earned: float
    timestamp: datetime
    diagnostic_hash: str
    cache_hit_rate: Optional[float] = None
    cache_miss_cost_ms: Optional[float] = None

class FirebaseManager:
    """Singleton Firebase manager with retry logic and connection pooling"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FirebaseManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._db = None
            self._credentials = None
            self._initialized = True
            
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((FirebaseError, ConnectionError))
    )
    def initialize(self, cred_path: Optional[str] = None, 
                  service_account_json: Optional[Dict] = None) -> None:
        """
        Initialize Firebase Admin SDK with fallback strategies.
        Priority: service_account_json > cred_path > environment variable
        """
        try:
            if firebase_admin._apps:
                logger.info("Firebase already initialized, reusing connection")
                self._db = firestore.client()
                return
            
            if service_account_json:
                logger.info("Initializing Firebase with provided JSON credentials")
                cred = credentials.Certificate(service_account_json)
            elif cred_path and Path(cred_path).exists():
                logger.info(f"Initializing Firebase with credential file: {cred_path}")
                cred = credentials.Certificate(cred_path)
            else:
                # Try environment variable
                env_cred = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
                if env_cred:
                    logger.info("Initializing Firebase with environment credentials")
                    cred_dict = json.loads(env_cred)
                    cred = credentials.Certificate(cred_dict)
                else:
                    # Last resort: try default application credentials
                    logger.warning("Using default Firebase application credentials")
                    cred = credentials.ApplicationDefault()
            
            # Initialize with explicit project ID if available
            project_id = os.getenv("FIREBASE_PROJECT_ID")
            if project_id:
                firebase_admin.initialize_app(cred, {'projectId': project_id})
            else:
                firebase_admin.initialize_app(cred)
            
            self._db = firestore.client()
            logger.info("Firebase initialized successfully", project_id=project_id)
            
            # Test connection
            self._test_connection()
            
        except Exception as e:
            logger.error("Firebase initialization failed", error=str(e), exc_info=True)
            raise
    
    def _test_connection(self):
        """Test Firestore connection with timeout"""
        import threading
        
        def connection_test():
            try:
                # Simple collection list to test connection
                collections = self._db.collections()
                list(collections)  # Force evaluation
                return True
            except:
                return False
        
        test_thread = threading.Thread(target=connection_test)
        test_thread.daemon = True
        test_thread.start()
        test_thread.join(timeout=5)
        
        if test_thread.is_alive():
            logger.warning("Firestore connection test timed out")
        else:
            logger.info("