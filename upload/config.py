import logging
from botoClients import ssm

"""
================= CONSTANTS =================
"""
class HTTP:
    OK = 200
    CREATED = 201
    BAD_REQUEST = 400
    NOT_FOUND = 404
    INTERNAL_ERROR = 500

MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_KEYS = {"img", "label", "weight"}
ALLOWED_LABELS = {"cat", "dog"}
ALLOWED_MIMES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp"
}

"""
================= Logging =================
"""

logger = logging.getLogger()
logger.setLevel(logging.INFO)

"""
================= SSM Helpers =================
"""

_paramCache = {}
def getSSMParam(paramName: str, default: str = None, decrypt: bool = False):
    if paramName in _paramCache:
        return _paramCache[paramName]
    
    try:
        responseValue = ssm.get_parameter(Name=paramName, WithDecryption=decrypt)["Parameter"]["Value"]
        _paramCache[paramName] = responseValue 
        return responseValue
    except Exception as _e:
        return default