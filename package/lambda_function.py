t unquote_plus

import boto3
import os
from io import BytesIO
from urllib.parse import unquote_plus
from PIL import Image

# Create S3 client
s3 = boto3.client("s3")

# Output S3 bucket
OUTPUT_BUCKET = "hanusai-lambda-output-2026"


def lambda_handler(event, context):

    # Read information from the S3 event
    record = event["Records"][0]

    source_bucket = record["s3"]["bucket"]["name"]
    object_key = unquote_plus(record["s3"]["object"]["key"])

    print(f"Original image: {object_key}")
    print(f"Source bucket: {source_bucket}")

    # Download the original image from S3
    response = s3.get_object(
        Bucket=source_bucket,
        Key=object_key
    )

    image_data = response["Body"].read()

    # Open the image using Pillow
    image = Image.open(BytesIO(image_data))

    # Resize while maintaining the aspect ratio
    image.thumbnail((300, 300))

    # Convert image to RGB for JPEG output
    image = image.convert("RGB")

    # Store the resized image in memory
    output_buffer = BytesIO()

    image.save(output_buffer, format="JPEG")

    # Generate output filename
    filename = os.path.splitext(os.path.basename(object_key))[0]
    output_key = f"resized/{filename}.jpg"

    # Upload resized image to output S3 bucket
    s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=output_key,
        Body=output_buffer.getvalue(),
        ContentType="image/jpeg"
    )

    print(f"Resized image uploaded: {output_key}")

    return {
        "statusCode": 200,
        "body": f"Image resized successfully: {output_key}"
    }
