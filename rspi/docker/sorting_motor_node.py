import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from std_msgs.msg import Int32MultiArray
import serial
import time

class SortingMotorNode(Node):
    def __init__(self):
        super().__init__('sorting_motor_node')
        
        # 1. AI 비전 결과 수신 & 초음파 거리 데이터 송신
        self.subscription = self.create_subscription(String, '/trash_class', self.trash_callback, 10)
        self.distance_pub = self.create_publisher(Int32MultiArray, '/bin_distances', 10)
        
        # 2. 아두이노 시리얼 연결
        try:
            self.serial_port = serial.Serial('/dev/ttyACM0', 115200, timeout=0.05)
            self.get_logger().info('✅ 아두이노 통신 연결 성공! (/dev/ttyACM0)')
            time.sleep(2) # 아두이노 리셋 대기
        except Exception as e:
            self.get_logger().error(f'❌ 아두이노 연결 실패 (도커 실행 시 --device /dev/ttyACM0 확인 필요): {e}')
            self.serial_port = None
            
        self.is_moving = False
        
        # 3. 0.1초마다 시리얼 데이터를 읽어오는 타이머 (논블로킹)
        self.create_timer(0.1, self.read_serial)

    def trash_callback(self, msg):
        # 모터가 이동 중이거나 아두이노가 없으면 새로운 명령 무시 (과부하 방지)
        if self.is_moving or not self.serial_port:
            return 
            
        trash_type = msg.data
        # 💡 처리할 5가지 깨끗한 쓰레기 목록
        valid_classes = ['plastic_clean', 'can_clean', 'glass_clean', 'paper_clean', 'vinyl_clean']
        
        if trash_type in valid_classes:
            self.get_logger().info(f'🚀 [작업 시작] 감지된 쓰레기: {trash_type}')
            self.is_moving = True
            
            # 아두이노로 해당 클래스 이름 전송 (끝에 \n 필수)
            command = f"{trash_type}\n"
            self.serial_port.write(command.encode('utf-8'))

    def read_serial(self):
        if not self.serial_port: return
        
        try:
            # 시리얼 버퍼에 데이터가 쌓여있으면 모두 읽어옴
            while self.serial_port.in_waiting > 0:
                line = self.serial_port.readline().decode('utf-8').strip()
                if not line: continue
                
                # A. 아두이노에서 작업 완료 신호가 오면 잠금 해제
                if line == "DONE":
                    self.get_logger().info('✅ [투하 완료] 다음 쓰레기 탐지 대기 중...\n')
                    self.is_moving = False
                    
                # B. 아두이노에서 초음파 센서 데이터가 오면 ROS 2로 전파
                elif line.startswith("DIST:"):
                    # 예: "DIST:10,20,-1,30,5" -> 거리 숫자만 파싱
                    dist_str = line.replace("DIST:", "")
                    dist_list = [int(float(x)) for x in dist_str.split(",")]
                    
                    msg = Int32MultiArray()
                    msg.data = dist_list
                    self.distance_pub.publish(msg)
                    # self.get_logger().info(f'초음파 센서 거리: {dist_list}') # 필요시 주석 해제하여 거리 확인
                    
        except Exception as e:
            pass # 시리얼 통신 중 발생하는 찌끄러기 노이즈는 무시

def main(args=None):
    rclpy.init(args=args)
    node = SortingMotorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()