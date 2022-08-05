from crypt import methods
from flask import Flask
from flask import request
from flask import jsonify
from flask_socketio import SocketIO
import flask
import pymysql
from db_connection import connect_db
from db_connection import close_db
# from flask_api import status
from secret import AWS_ACCESS_KEY, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME, AWS_S3_BUCKET_REGION
from secret import AWS_REQUEST_SQS_URL, AWS_RESPONSE_SQS_URL
import boto3
import logging
import json
from botocore.exceptions import ClientError
from sqs_connection import get_request_queue, get_response_queue

app = Flask(__name__)
socket_io = SocketIO(app)

s3_client = boto3.client('s3', aws_access_key_id = AWS_ACCESS_KEY, aws_secret_access_key = AWS_SECRET_ACCESS_KEY, region_name = AWS_S3_BUCKET_REGION)
logger = logging.getLogger(__name__)
# Getting Request Queue from SQS
request_queue = get_request_queue()
response_queue = get_response_queue()

@app.route('/')
def hello():
    return "Hello World!"

# ==================
## 리뷰 생성 API
# ==================
@app.route('/reviews', methods=['POST'])
def create_review():
    param = request.get_json()

    ## AWS RDS 연결
    conn, cur = connect_db()

    ## insert data - review table
    insert_sql = "insert into review (user_id, content) values (%s, %s);"
    review_values = (param['user_id'], param['review'])
    
    cur.execute(insert_sql, review_values)
    conn.commit()

    ## AWS RDS 연결 해제
    close_db(conn, cur)

    response_result = { 'result' : 'success' }

    # print(param['review'])
    return jsonify(response_result), 201

# ==================
## 이미지 업로드 API
# ==================
@app.route('/upload', methods=['POST'])
def upload():
    # user 테이블에 튜플 insert
    conn, cur = connect_db()
    insert_sql = "insert into user values () ;"
    cur.execute(insert_sql)
    user_id = str(cur.lastrowid)
    conn.commit()

    path = '/' + str(user_id) + '/' + 'profile.jpeg'
    try:
        upload_url = s3_client.generate_presigned_url('put_object',
                                                    Params={'Bucket': AWS_S3_BUCKET_NAME,
                                                            'Key': path},
                                                    ExpiresIn=1000 * 60 * 3) # 3분
    except ClientError as e:
        logging.error(e)
        return None
    
    close_db(conn, cur)

    response = { 'user_id': user_id, 'upload_url': upload_url }
    return jsonify(response)

# ==================
## 테스트용
# ==================
@app.route('/download', methods=['POST'])
def download():
    path = '/1/profile.jpeg'
    try:
        download_url = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': AWS_S3_BUCKET_NAME,
                                                            'Key': path},
                                                    ExpiresIn=1000 * 60 * 3) # 3분
    except ClientError as e:
        logging.error(e)
        return None
    response = { 'download_url': download_url }
    return jsonify(response)

# ==================
## Inference 요청 API
# ==================
@app.route('/inference', methods=['POST'])
def hairclip_inference():
    params = request.get_json()
    param_user_id = params['user_id']

    message_body_json = {
        'user_id' : param_user_id
    }

    message_body_str = json.dumps(message_body_json)

    try:
        # Send message to Request Queue
        send_result = request_queue.send_message(MessageBody=message_body_str, QueueUrl=AWS_REQUEST_SQS_URL)


    except ClientError as error:
        logger.exception("Send message failed! (Message body : { user_id : %s })", param_user_id)

        raise error
    
    return send_result

# ==================
## Response_queue로부터 inference 완료 메시지 받는 API (using Socket)
# ==================
@app.route('/receive')
def send():
    
    try:
        messages = response_queue.meta.client.receive_message(
            QueueUrl=AWS_RESPONSE_SQS_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=2,
            MessageAttributeNames=['All']
        )
        if 'Messages' not in messages:
            logger.info('message not in response_queue!')
            fail_result = {'result': 'fail' }
            return jsonify(fail_result) 
        

        # for message in messages:
        for message in messages['Messages']:
            data = message['Body']
            data = json.loads(data)
            
            param_user_id = data["user_id"]

            socket_io.send('{}'.format(param_user_id))

            print("Message from Request Queue : ", data)    
            
            response_queue.meta.client.delete_message(
                QueueUrl=AWS_RESPONSE_SQS_URL,
                ReceiptHandle=message['ReceiptHandle']
            )
            success_result = {'result': 'success', 'user_id': '{}'.format(param_user_id) }
            return jsonify(success_result) 

    except ClientError as error:
        logger.exception("receive message from Response queue failed! (Message body : { user_id : %s })", param_user_id)
        print('error')
        raise error

if __name__ == '__main__':
    socket_io.run(app, host='0.0.0.0', port=5000, debug=True)
# FLASK_APP=app.py flask run