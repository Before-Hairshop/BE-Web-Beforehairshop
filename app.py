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
# socket_io = SocketIO(app)
# socket_io.init_app(app, cors_allowed_origins="*")

s3_client = boto3.client('s3', aws_access_key_id = AWS_ACCESS_KEY, aws_secret_access_key = AWS_SECRET_ACCESS_KEY, region_name = AWS_S3_BUCKET_REGION)
logger = logging.getLogger(__name__)
# Getting Request Queue from SQS
request_queue = get_request_queue()
response_queue = get_response_queue()

@app.route('/api')
def hello():
    return "Hello World!"

# ==================
## 리뷰 생성 API
# ==================
@app.route('/api/reviews', methods=['POST'])
def create_review():
    param = request.get_json()

    # ## AWS RDS 연결
    conn, cur = connect_db()

    ## insert data - review table
    insert_sql = "insert into review (user_id, point, content) values (%s, %s, %s);"
    review_values = (param['user_id'], param['point'], param['review'])
    
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
@app.route('/api/upload', methods=['POST'])
def upload():
    # user 테이블에 튜플 insert
    conn, cur = connect_db()
    insert_sql = "insert into user (status) values (%s) ;"
    cur.execute(insert_sql, (0))
    user_id = str(cur.lastrowid)
    conn.commit()

    path = '/' + str(user_id) + '/' + 'profile.jpg'
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
@app.route('/api/download', methods=['POST'])
def download():
    path = '/1/profile.jpg'
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
@app.route('/api/inference', methods=['POST'])
def hairclip_inference():
    params = request.get_json()
    param_user_id = params['user_id']

    message_body_json = {
        'user_id' : param_user_id
    }

    message_body_str = json.dumps(message_body_json)
    success_response = {"result" : "success"}
    fail_response = {"result" : "fail"}
             
    try:
        # Send message to Request Queue
        request_queue.send_message(MessageBody=message_body_str, QueueUrl=AWS_REQUEST_SQS_URL)
    except ClientError as error:
        logger.exception("Send message failed! (Message body : { user_id : %s })", param_user_id)
        return jsonify(fail_response)
    
    return jsonify(success_response)

# ==================
## Response_queue로부터 inference 완료 메시지 받는 API (using Socket)
# ==================
@app.route('/api/receive', methods=['GET'])
def send():
    conn, cur = connect_db()
    try:
        messages = response_queue.meta.client.receive_message(
            QueueUrl=AWS_RESPONSE_SQS_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=2,
            MessageAttributeNames=['All']
        )
        if 'Messages' not in messages:
            logger.info('message not in response_queue!')
            progress_result = {'result': 'progress' }
            return jsonify(progress_result) 
        

        # for message in messages:
        for message in messages['Messages']:
            data = message['Body']
            data = json.loads(data)
            
            param_result = data["result"]
            param_user_id = data["user_id"]

            # success시에, user status 값 1로 변경시킨다.
            if param_result == "success":
                success_update_sql = "update user set status = %s where id = %s;"
                cur.execute(success_update_sql, (1, param_user_id))
                conn.commit()
            elif param_result == "fail":
                fail_update_sql = "update user set status = %s where id = %s;"
                cur.execute(fail_update_sql, (-1, param_user_id))
                conn.commit()
            # socket_io.send('{}'.format(param_user_id))

            print("Message from Request Queue : ", data)    
            
            response_queue.meta.client.delete_message(
                QueueUrl=AWS_RESPONSE_SQS_URL,
                ReceiptHandle=message['ReceiptHandle']
            )
            success_result = {'result': param_result }
            return jsonify(success_result) 

    except ClientError as error:
        logger.exception("receive message from Response queue failed! (Message body : { user_id : %s })", param_user_id)
        print('error')
        raise error


# ==================
## 가상 헤어스타일링 이미지 요청 API
# ==================
@app.route('/api/getImage', methods=['POST'])
def get_image_url():
    params = request.get_json()
    param_user_id = params['user_id']
    param_hair_style = params['hair_style']
    param_hair_color = params['hair_color']

    path = "/" + str(param_user_id) + "/"
    if param_hair_style == "None" and param_hair_color == "None":
        path = path + "color/black hair"
    elif param_hair_style != "None" and param_hair_color == "None":
        path = path + "both/" + param_hair_style + " hairstyle-black hair"
    elif param_hair_style == "None" and param_hair_color != "None":
        path = path + "color/" + param_hair_color + " hair"
    else:
        path = path + "both/" + param_hair_style + " hairstyle-" + param_hair_color + " hair"
    
    path = path + ".jpg"

    
    fail_response = {"result" : "fail", "presigned_url" : "None"}
    try:
        url = s3_client.generate_presigned_url(ClientMethod='get_object', Params={'Bucket': AWS_S3_BUCKET_NAME, 'Key': path}, ExpiresIn=3600)
        success_response = {"result" : "success", "presigned_url" : url}

        return jsonify(success_response)
    except ClientError as e:
        logging.error(e)
        return jsonify(fail_response) 

@app.route('/api/inference-check', methods=['POST'])
def inference_check():
    params = request.get_json()
    param_user_id = params['user_id']

    check_sql = "select * from user where id = %s;"
    
    conn, cur = connect_db()
    cur.execute(check_sql, (param_user_id))
    rows = cur.fetchall()

    success_response = {"result" : "success"}
    progress_response = {"result" : "progress"}
    fail_response = {"result" : "fail"}

    for row in rows:
        if row['status'] == 1:
            return jsonify(success_response)
        elif row['status'] == 0:
            return jsonify(progress_response)
        else:
            return jsonify(fail_response)




if __name__ == '__main__':
    app.run(host='0.0.0.0', port='5000', debug=True)
# FLASK_APP=app.py flask run