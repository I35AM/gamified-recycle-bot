import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32MultiArray  # 💡 String에서 Int32MultiArray로 수정!
from flask import Flask
from flask_socketio import SocketIO
import threading

app = Flask(__name__)
# 로컬 HTML 파일에서 접근할 수 있도록 CORS 허용
socketio = SocketIO(app, cors_allowed_origins="*")

MAX_DIST = 20.0  # 0% (비어있음 기준)
MIN_DIST = 5.0   # 100% (가득 참 기준)

class UIBridgeNode(Node):
    def __init__(self):
        super().__init__('ui_bridge_node')
        # 💡 메시지 타입을 Int32MultiArray로 일치시킴
        self.subscription = self.create_subscription(
            Int32MultiArray,
            '/bin_distances',
            self.listener_callback,
            10)
        # 아두이노의 target_bin 인덱스(0~4)와 완벽히 동일한 순서
        self.bin_types = ['plastic', 'can', 'glass', 'paper', 'vinyl']

    def listener_callback(self, msg):
        try:
            # 💡 msg.data 자체가 이미 숫자 리스트이므로 복잡한 변환 제거!
            distances = msg.data
            
            for i, dist in enumerate(distances):
                if i >= len(self.bin_types): break
                bin_type = self.bin_types[i]
                
                # 측정 실패(-1) 처리
                if dist < 0 or dist >= MAX_DIST:
                    percent = 0
                elif dist <= MIN_DIST:
                    percent = 100
                else:
                    percent = int(((MAX_DIST - dist) / (MAX_DIST - MIN_DIST)) * 100)
                    
                # Socket.IO를 통해 HTML로 실시간 데이터 전송
                socketio.emit('sensor_data', {'type': bin_type, 'percent': percent})
                
        except Exception as e:
            self.get_logger().error(f"거리 데이터 파싱 에러: {e}")

def ros_spin_thread():
    rclpy.init()
    node = UIBridgeNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    # ROS 2 통신은 백그라운드 스레드에서 무한 반복
    threading.Thread(target=ros_spin_thread, daemon=True).start()
    
    # 메인 스레드에서는 Flask-SocketIO 웹 서버 실행
    print("🌐 UI 연동 웹 서버 시작 (포트: 5001)")
    socketio.run(app, host='0.0.0.0', port=5001)
