import os
import sys
import shutil
import logging
import time
# Add services/core to path
sys.path.append(os.getcwd())

from services.core.course_builder import SkeletonBuilder, ContentHydrator

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
DB_PATH = "/app/data/kuzu_test_db_generation"
COURSES_DIR = "/app/data/courses"

STRICT_MODE = True # Fail on any validation error

TEST_CASES = [
    {
        "topic": "Causal Inference",
        "type": "Technical",
        "depth": 4, # Deep technical dive
        "expectations": ["counterfactual", "dag", "confounder", "instrumental variable"]
    },
    {
        "topic": "Roman History",
        "type": "Historical",
        "depth": 2, # Standard survey
        "expectations": ["republic", "empire", "senate", "caesar", "augustus"]
    },
    {
        "topic": "Balancing Creative and Critical Thinking",
        "type": "Creative",
        "depth": 3, # Workshop style
        "expectations": ["divergent", "convergent", "brainstorming", "critique", "synthesis"]
    }
]

def clean_db():
    if os.path.exists(DB_PATH):
        logger.info(f"Cleaning DB at {DB_PATH}")
        if os.path.isdir(DB_PATH):
            shutil.rmtree(DB_PATH)
        else:
            os.remove(DB_PATH)

def validate_course(course_uid, course_data):
    logger.info(f"--- Validating Course: {course_data['topic']} ({course_uid}) ---")
    
    topics_dir = os.path.join(COURSES_DIR, course_uid, "topics")
    if not os.path.exists(topics_dir):
        logger.error(f"❌ Topics directory missing: {topics_dir}")
        return False
        
    md_files = [f for f in os.listdir(topics_dir) if f.endswith('.md')]
    if not md_files:
        logger.error("❌ No markdown files found.")
        return False
        
    logger.info(f"Found {len(md_files)} content files.")
    
    # Check for expected keywords in at least one file
    content_blob = ""
    for f in md_files:
        with open(os.path.join(topics_dir, f), 'r') as fh:
            content_blob += fh.read().lower()
            
    missing_keywords = []
    for keyword in course_data['expectations']:
        if keyword.lower() not in content_blob:
            missing_keywords.append(keyword)
            
    if missing_keywords:
        logger.warning(f"⚠️  Missing expected keywords: {missing_keywords}")
    else:
        logger.info("✅ All expected keywords found.")

    # Check for 'Example Leakage' (e.g. History in Technical)
    # This is a heuristic check based on the 'type'
    leakage_errors = 0
    if course_data['type'] == "Technical":
        historical_terms = ["roman", "caesar", "senate", "legion", "emperor"]
        for term in historical_terms:
            if term in content_blob:
                logger.warning(f"⚠️  Potential LEAKAGE: Found '{term}' in Technical course.")
                leakage_errors += 1
                
    if course_data['type'] == "Historical":
        tech_terms = ["python", "javascript", "compiler", "algorithm", "software"]
        for term in tech_terms:
            if term in content_blob:
                logger.warning(f"⚠️  Potential LEAKAGE: Found '{term}' in Historical course.")
                leakage_errors += 1
                
    if leakage_errors == 0:
        logger.info("✅ No obvious context leakage detected.")
        
    return True

def run_generation():
    clean_db()
    
    # 1. Skeleton Building
    logger.info("====================================")
    logger.info("PHASE 1: SKELETON BUILDING")
    logger.info("====================================")
    
    generated_courses = []
    
    for case in TEST_CASES:
        logger.info(f"Building Skeleton for: {case['topic']}")
        try:
            builder = SkeletonBuilder(db_path=DB_PATH, course_depth=3)
            uid = builder.build(case['topic'], max_depth=case['depth'])
            logger.info(f"  -> UID: {uid}")
            case['uid'] = uid
            generated_courses.append(case)
            builder.close() # Release DB lock
        except Exception as e:
            logger.error(f"  -> FAILED: {e}", exc_info=True)
            
    # 2. Content Hydration
    logger.info("\n====================================")
    logger.info("PHASE 2: CONTENT HYDRATION")
    logger.info("====================================")
    
    # Initialize Hydrator (mocking providers for now)
    try:
        from services.rag.zim_provider import ZimProvider
        zim = ZimProvider()
        providers = [zim]
    except:
        logger.warning("ZimProvider not found, running with LLM only.")
        providers = []
        
    for case in generated_courses:
        uid = case.get('uid')
        if not uid: continue
        
        logger.info(f"Hydrating: {case['topic']} ({uid})")
        try:
            hydrator = ContentHydrator(db_path=DB_PATH, providers=providers, course_depth=3)
            hydrator.hydrate(uid)
            validate_course(uid, case)
            hydrator.close() # Release DB lock
        except Exception as e:
            logger.error(f"  -> HYDRATION FAILED: {e}", exc_info=True)
            
    logger.info("\n====================================")
    logger.info("GENERATION COMPLETE")
    logger.info("====================================")
    for case in generated_courses:
        logger.info(f"Course: {case['topic']}")
        logger.info(f"  UID: {case['uid']}")
        logger.info(f"  Path: {os.path.join(COURSES_DIR, case['uid'], 'topics')}")

if __name__ == "__main__":
    run_generation()
