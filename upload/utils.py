import config as cfg
from typing import Callable, Dict, Any, Union, List
import json, re, time, os, imghdr
from decimal import Decimal
from botoClients import s3, dynamodb

def responder(statusCode: int, bodyMessage: Dict[str, Any]) -> Dict[str, Any]:
    """
    Constructs a standardized HTTP response for API Gateway.

    This function wraps a given status code and response body into the format 
    expected by AWS Lambda proxy integrations.

    Args:
        statusCode (int): The HTTP status code to return (e.g., 200, 400, 500).
        bodyMessage (Dict[str, Any]): A dictionary representing the response payload.

    Returns:
        Dict[str, Any]: A dictionary with `statusCode`, `headers`, and a JSON-encoded `body`.
    """
    return {
        "statusCode": statusCode,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(bodyMessage)
    }

def tryCatchHandler(func: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    """
    A decorator to wrap Lambda handler functions with standardized exception handling.

    This decorator ensures that:
      - Known client-side errors (e.g., invalid input) are caught as `ValueError`
        and responded with a 400 Bad Request and a sanitized error message.
      - Unhandled server-side exceptions are logged and responded with a generic
        500 Internal Server Error message without exposing stack traces to the client.

    Logs:
      - Client errors as warnings
      - Server errors as full stack traces for diagnostics

    Args:
        func (Callable[..., Dict[str, Any]]): The Lambda function to wrap.

    Returns:
        Callable[..., Dict[str, Any]]: A new function that wraps `func` with error handling logic.
    """
    def wrapper(*args, **kwargs) -> Dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except ValueError as ve:
            # client-side errors
            cfg.logger.warning(f"ValueError: {ve}")
            return responder(400, {"error": "Bad request"})
        except Exception as ge:
            # internal error 
            cfg.logger.error(f"Unhandled Exception: {ge}", exc_info=True)
            return responder(500, {"error": "Internal server error"})
    return wrapper

def bodyParser(requestBody: bytes, requestHeader: Dict[str, str]) -> Union[Dict[str, Any], Dict[str, str]]:
    """
    Parses a multipart/form-data HTTP request body and extracts fields and files.

    Args:
        requestBody (bytes): The raw binary body of the incoming HTTP request.
        requestHeader (Dict[str, str]): Dictionary of request headers, including 'Content-Type'.

    Returns:
        Union[Dict[str, Any], Dict[str, str]]:
            - If parsing is successful, returns a dictionary where keys are field names and 
              values are dictionaries describing either a "file" or "field".
            - If the request is invalid or improperly formatted, returns a dictionary with an error message.

    Notes:
        - Supports standard text fields and file fields (with filename, content type, and binary data).
        - Enforces a maximum file upload size defined by `cfg.MAX_UPLOAD_SIZE`.
        - Case-insensitive parsing for headers and content type detection.
        - Ensures each multipart section includes a valid 'name' and optionally a 'filename' and 'Content-Type' header.
    """
    # case-insensitive header access
    contentType = requestHeader.get("Content-Type") or requestHeader.get("content-type")
    
    if not contentType or "multipart/form-data" not in contentType:
        return cfg.responder(cfg.HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })
    
    # attempt to fetch boundary value
    bdrIDX = contentType.find("boundary=")
    if bdrIDX == -1:
        return cfg.responder(cfg.HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })
    bdrValue = contentType.split("boundary=")[-1].strip()
    bdrBytes = f"--{bdrValue}".encode()

    # split the body by the boundary
    bodyParts = requestBody.split(bdrBytes)

    parsedBody = {}

    for part in bodyParts:
        # clean-up
        part = part.strip(b"\r\n")
        
        if not part or part == b"--":
            continue

        # split the part into headers (part_header) & body (part_data)
        if b"\r\n\r\n" not in part:
            return cfg.responder(cfg.HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })
        
        part_header, part_data = part.split(b"\r\n\r\n", 1)

        # extract and safely decode common 'name' field from the header
        nameFieldMatch = re.search(br';\s*name="([^"]+)"', part_header)
        if not nameFieldMatch:
            return cfg.responder(cfg.HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })

        nameFieldMatch = nameFieldMatch.group(1).decode("utf-8", errors="ignore")

        # check if it's a file or regular part 
        if b"filename=" in part:
            # file size check
            if len(part_data) > cfg.MAX_UPLOAD_SIZE:
                return {"error": f"file size exceeds limit for field '{nameFieldMatch}'"}

            filenameFieldMatch = re.search(br';\s*filename="([^"]+)"', part_header)
            contentTypeFieldMatch = re.search(br"Content-Type:\s*([^\r\n]+)", part_header)

            # skip if the part if no filename or content field is found
            if not filenameFieldMatch or not contentTypeFieldMatch:
                return cfg.responder(cfg.HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })

            parsedBody[nameFieldMatch] = {
                "type": "file",
                "filename": filenameFieldMatch.group(1).decode("utf-8", errors="ignore"),
                "contentType": contentTypeFieldMatch.group(1).decode("utf-8", errors="ignore"),
                "data": part_data
            }
        else:
            parsedBody[nameFieldMatch] = {
                "type": "field",
                "value": part_data.decode("utf-8", errors="ignore") 
            }
    
    return parsedBody

def bodyValidator(req_body: Dict[str, Any]) -> Union[bool, Dict[str, Any]]:
    """
    Validates the structure and content of a multipart HTTP request body.

    Args:
        req_body (Dict[str, Any]): The parsed request body dictionary containing 
        'img', 'label', and optionally 'weight' fields. Each field is expected to follow 
        a specific structure (e.g., 'type': 'file' or 'field').

    Returns:
        Union[bool, Dict[str, Any]]:
            - True if the request body is valid.
            - A response dictionary with status code and error message if validation fails.

    Notes:
        - 'img' must be a valid image file of type JPEG, PNG, or WEBP.
        - 'label' must be a string field and one of the allowed labels (e.g., "cat", "dog").
        - 'weight' is optional but must be a float strictly between 0.0 and 1.0 if provided.
    """
    # check for unexpected fields
    for key in req_body:
        if key not in cfg.ALLOWED_KEYS:
            return responder(cfg.HTTP.BAD_REQUEST, {"error": f"Unexpected field '{key}' in request body"})
    
    img = req_body.get("img")
    label = req_body.get("label")
    weight = req_body.get("weight")

    # required regular fields exist
    # --- img ---
    if not img or img.get("type") != "file":
        return responder(cfg.HTTP.BAD_REQUEST, {"error": "'img' must be a valid file"})
    
    imgData = img.get("data")
    imgMIME = img.get("contentType", "").lower()
    expectedType = cfg.ALLOWED_MIMES.get(imgMIME)
    
    if not expectedType:
        magicType = imghdr.what(None, h=imgData)

        for mime, ext in cfg.ALLOWED_MIMES.items():
            if ext == magicType:
                imgMIME = mime
                expectedType = magicType
                break
        else:
            expectedType = None

    if not imgData or not expectedType:
        return responder(cfg.HTTP.BAD_REQUEST, {"error": f"'img' must be {sorted(cfg.ALLOWED_MIMES.keys())}"})

    # --- label ---
    if not label or label.get("type") != "field":
        return responder(cfg.HTTP.BAD_REQUEST, {"error": "'label' must be a valid field"})

    labelValue = label["value"].strip().lower()
    if labelValue not in cfg.ALLOWED_LABELS:
        return responder(cfg.HTTP.BAD_REQUEST, {
            "error": f"'label' must be one of {sorted(cfg.ALLOWED_LABELS)}"
        })

    # --- weight (optional) ---
    if weight and weight.get("type") == "field":
        weightValue = weight.get("value")
        try:
            weight_num = float(weightValue)
            if not (0.0 < weight_num < 1.0):
                return responder(cfg.HTTP.BAD_REQUEST, {"error": "'weight' must be a number between 0.0 (exclusive) and 1.0 (exclusive)"})
        except (ValueError, TypeError):
            return responder(cfg.HTTP.BAD_REQUEST, {"error": "'weight' must be a valid float (e.g., 0.5, 0.9)"})
    
    return True
    
def getChunksInfo(label: str) -> dict[str, Union[str, List[int], int]]:
    """
    Retrieves metadata for a given label from the DynamoDB chunk info table.

    Args:
        label (str): The label associated with the image category (e.g., "cat", "dog").

    Returns:
        dict[str, Union[str, List[int], int]]: A dictionary containing chunk metadata,
        including 'chunkMax', 'chunkThreshold', 'chunksNumber', 'chunkVolume', and 'activeChunks'.

    Raises:
        RuntimeError: If the data cannot be retrieved from DynamoDB.

    Notes:
        - The table name is retrieved from AWS SSM using a predefined parameter.
        - Assumes the item with the given label exists in the table, i.e. precondition of label creation
    """
    infoTableName = cfg.getSSMParam("/pet-api/production/dynamoDB/chunk-info-table-name", default=os.environ.get("fb_chunkInfoTableName"))
    infoTable = dynamodb.Table(infoTableName)

    try:
        dynamoResponse = infoTable.get_item(Key={"label": label})
        return dynamoResponse["Item"]
    except Exception as _e:
        raise RuntimeError("Failed to retrive data from DynamoDB")

def selectAndModifyChunk(chunkMetadata: dict[str, Union[str, List[Decimal]]], label: str) -> None:
    """
    Selects an appropriate chunk for the new image entry based on current volume thresholds,
    and updates the chunk metadata in DynamoDB accordingly.

    If a reusable chunk (not exceeding `chunkMax` or `chunkThreshold`) exists, it is updated
    with incremented volume. If no such chunk exists, a new chunk is created and appended
    to the metadata structure (`chunkVolume`, `activeChunks`, `chunksNumber`).

    Args:
        chunkMetadata (dict): Dictionary containing chunk management fields from DynamoDB:
            - 'chunkMax': Maximum capacity per chunk.
            - 'chunkThreshold': Soft threshold to prefer reusing existing chunks.
            - 'chunksNumber': Total number of chunks so far.
            - 'chunkVolume': List representing volume of each chunk.
            - 'activeChunks': List of indices of chunks currently accepting entries.
        label (str): The image label (e.g., "cat", "dog") used as the primary key in the table.

    Returns:
        int: The ID (index) of the chunk selected or created for storing the image entry.

    Raises:
        RuntimeError: If the DynamoDB update operation fails during selection or creation.
    """
    chunkMax = int(chunkMetadata["chunkMax"])
    chunksNumber = int(chunkMetadata["chunksNumber"])
    chunkThreshold = int(chunkMetadata["chunkThreshold"])
    chunkVolume = chunkMetadata["chunkVolume"]
    activeChunks = chunkMetadata["activeChunks"]


    infoTableName = cfg.getSSMParam("/pet-api/production/dynamoDB/chunk-info-table-name", default=os.environ.get("fb_chunkInfoTableName"))
    infoTable = dynamodb.Table(infoTableName)
    
    # find reusable chunk
    for idx in activeChunks:
        idx = int(idx)
        volume = int(chunkVolume[idx])
        if volume <= chunkThreshold or volume <= chunkMax :
            newVolume = volume + 1
        
            removeFromActive = newVolume >= chunkMax
            updateExpr = f"SET chunkVolume[{idx}] = :newVolume"
            exprValues = {":newVolume": Decimal(newVolume)}

            if removeFromActive:
                updateExpr += f" REMOVE activeChunks[{activeChunks.index(idx)}]"

            try:
                infoTable.update_item(
                    Key={"label": label},
                    UpdateExpression=updateExpr,
                    ExpressionAttributeValues=exprValues
                )
            except Exception as e:
                    raise RuntimeError("Failed to update data within DynamoDB")

            return idx
    
    # no reusable chunk, create new one
    try:
        infoTable.update_item(
            Key={"label": label},
            UpdateExpression="""
                SET chunksNumber = :nextChunk,
                    chunkVolume = list_append(chunkVolume, :zeroList),
                    activeChunks = list_append(activeChunks, :newChunk)
            """,
            ExpressionAttributeValues={
                ":nextChunk": Decimal(chunksNumber + 1),
                ":zeroList": [Decimal(1)],
                ":newChunk": [Decimal(chunksNumber)]
            }
        )
    except Exception as e:
        raise RuntimeError("Failed to update data within DynamoDB")

    return chunksNumber


def appendToChunkFile(label: str, chunkId: int, newEntry: dict) -> None:
    """
    Appends a new image metadata entry to the specified chunk in the DynamoDB image chunks table.

    This function constructs a new item with the given label, chunk ID, S3 key, and weight,
    and inserts it into the image chunks table. Each item represents one uploaded image 
    and is organized under its respective chunk.

    Args:
        label (str): The image label used as the partition key in the table (e.g., "cat", "dog").
        chunkId (int): The chunk index where the image metadata should be stored.
        newEntry (dict): Dictionary containing:
            - 's3key': The S3 key/path of the uploaded image.
            - 'weight': The selection weight for this image (float between 0.0 and 1.0).

    Raises:
        RuntimeError: If the DynamoDB `put_item` operation fails.
    """
    chunksTableName = cfg.getSSMParam("/pet-api/production/dynamoDB/image-chunks-table-name", default=os.environ.get("fb_chunkImageTableName"))
    chunksTable = dynamodb.Table(chunksTableName)
    try:
        item = {
            "label": label,
            "chunkNumber": chunkId,
            "s3key": newEntry["s3key"],
            "weight": newEntry["weight"] 
        }
        
        chunksTable.put_item(Item=item)
    except Exception as _e:
        raise RuntimeError("Failed to insert into DynamoDB")

def _append_timestamp(filename: str) -> str:
    """
    Appends a UNIX timestamp to the base name of the given filename.

    Args:
        filename (str): The original filename (e.g., "image.jpg").

    Returns:
        str: The filename with a timestamp appended before the extension
             (e.g., "image_1721382021.jpg").
    """
    name, ext = os.path.splitext(filename) 
    timestamp = int(time.time()) 
    return f"{name}_{timestamp}{ext}"

def s3Upload(fileDict: Dict[str, str], label: str) -> Union[str, None]:
    """
    Uploads an image to the appropriate S3 bucket and returns its public URL.

    Args:
        fileDict (Dict[str, str]): A dictionary containing file information:
            - 'filename': Original file name (used for naming in S3)
            - 'data': Binary content of the file
            - 'contentType': MIME type of the file (e.g., "image/png")
        label (str): The image label ("cat", "dog", etc.), used as the prefix in the S3 key.

    Returns:
        Union[str, None]: The public URL of the uploaded image in the S3 bucket,
                          or None if the upload fails.
    """
    try:
        newFilename = _append_timestamp(fileDict.get("filename"))
        key = f'{label}/{newFilename}'
        s3BucketName = cfg.getSSMParam("/pet-api/production/s3/upload-bucket-name", default=os.environ.get("fb_S3BucketName"))

        s3.put_object(
            Bucket=s3BucketName,
            Key=key,
            Body=fileDict.get("data"),
            ContentType=fileDict.get("contentType")
        )

        return f"https://{s3BucketName}.s3.amazonaws.com/{key}"

    except Exception as _e:
        return None
    

def s3Delete(s3Key: str) -> bool:
    """
    Deletes an object from the configured S3 upload bucket.

    Args:
        s3Key (str): The full key (path) of the object to delete.

    Returns:
        bool: True if deletion succeeds, False otherwise.
    """
    try:
        s3BucketName = cfg.getSSMParam("/pet-api/production/s3/upload-bucket-name", default=os.environ.get("fb_S3BucketName"))
        s3.delete_object(Bucket=s3BucketName, Key=s3Key)
        return True
    except Exception as _e:
        return False