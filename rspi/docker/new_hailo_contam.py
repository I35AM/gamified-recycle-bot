#new_hailo_contam.py
import requests
import sys
import time
import cv2
import threading
import numpy as np
import datetime 
from collections import Counter, deque
from hailo_platform import HEF, VDevice, FormatType, InferVStreams
import hailo_platform

# ROS 2 라이브러리 추가
import rclpy
from std_msgs.msg import String
import time

# 🌐 [새로 추가] AWS 백엔드 서버 설정
SERVER_URL = "AWS 백앤드 서버 주소"


# 오버플로우 경고 문구 억제
np.seterr(over='ignore')

# 🔥 [환경 맞춤형 튜닝] 확신도 임계값 설정
CONFIDENCE_THRESHOLD = 0.65 
SMOOTHING_FRAMES = 5  # 🔥 [핵심] 최근 5프레임의 결과를 모아서 다수결 투표

CLASS_NAMES = [
    'plastic_clean', 'plastic_dirty', 
    'can_clean', 'can_dirty', 
    'glass_clean', 'glass_dirty', 
    'paper_clean', 'paper_dirty', 
    'vinyl_clean', 'vinyl_dirty'
]

TARGET_CLASSES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

TARGET_COLORS = {
    0: (0, 255, 0),       # plastic_clean
    1: (0, 0, 255),       # plastic_dirty
    2: (255, 255, 0),     # can_clean
    3: (0, 165, 255),     # can_dirty
    4: (255, 0, 0),       # glass_clean
    5: (255, 0, 255),     # glass_dirty
    6: (144, 238, 144),   # paper_clean
    7: (0, 0, 139),       # paper_dirty
    8: (230, 216, 173),   # vinyl_clean
    9: (0, 255, 255)      # vinyl_dirty
}

# ==========================================
# 🧠 프레임 스무딩을 위한 객체 추적 알고리즘
# ==========================================
def compute_iou(box1, box2):
    """두 박스가 겹치는 비율(IoU)을 계산하여 동일한 물체인지 판별"""
    x1_inter = max(box1[0], box2[0])
    y1_inter = max(box1[1], box2[1])
    x2_inter = min(box1[2], box2[2])
    y2_inter = min(box1[3], box2[3])
    
    inter_area = max(0, x2_inter - x1_inter) * max(0, y2_inter - y1_inter)
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])
    
    iou = inter_area / float(box1_area + box2_area - inter_area + 1e-6)
    return iou

class ObjectTracker:
    def __init__(self):
        self.tracks = []
        
    def update(self, detections):
        updated_tracks = []
        matched_detection_indices = set()
        
        for track in self.tracks:
            best_iou = 0
            best_det_idx = -1
            
            for i, det in enumerate(detections):
                if i in matched_detection_indices:
                    continue
                iou = compute_iou(track['box'], det[:4])
                if iou > 0.4: # IoU 임계값 (40% 이상 겹치면 같은 물체로 인정)
                    if iou > best_iou:
                        best_iou = iou
                        best_det_idx = i
                        
            if best_det_idx != -1:
                # 같은 물체를 찾았으면, 과거 기억에 새로운 예측 결과를 추가!
                det = detections[best_det_idx]
                track['box'] = det[:4]
                track['class_history'].append(det[5])
                track['score_history'].append(det[4])
                track['missed'] = 0
                updated_tracks.append(track)
                matched_detection_indices.add(best_det_idx)
            else:
                # 화면에서 사라졌다면 놓친 프레임 증가
                track['missed'] += 1
                if track['missed'] < 3: # 3프레임 이상 안보이면 기억 삭제
                    updated_tracks.append(track)
                    
        # 처음 보는 새로운 물체 추가
        for i, det in enumerate(detections):
            if i not in matched_detection_indices:
                new_track = {
                    'box': det[:4],
                    'class_history': deque([det[5]], maxlen=SMOOTHING_FRAMES),
                    'score_history': deque([det[4]], maxlen=SMOOTHING_FRAMES),
                    'missed': 0
                }
                updated_tracks.append(new_track)
                
        self.tracks = updated_tracks
        return self.tracks

# ==========================================

class ThreadedCamera:
    def __init__(self, source):
        self.cap = cv2.VideoCapture(source)
        
        # 웹캠 자동 게인/노출 제어 (노이즈 방지)
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1) 
        self.cap.set(cv2.CAP_PROP_GAIN, 0)          
        
        self.ret = False
        self.frame = None
        self.running = True
        self.lock = threading.Lock()
        
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.ret = ret
                    self.frame = frame
            else:
                time.sleep(0.001)

    def read(self):
        with self.lock:
            return self.ret, self.frame

    def release(self):
        self.running = False
        self.cap.release()

def decode_yolov8_stride(box_tensor, score_tensor, stride, score_thresh=0.25):
    box_tensor = np.squeeze(box_tensor, axis=0)
    score_tensor = np.squeeze(score_tensor, axis=0)
    
    score_tensor = np.clip(score_tensor, -32, 32)
    scores = 1 / (1 + np.exp(-score_tensor))
    class_ids = np.argmax(scores, axis=-1)
    max_scores = np.max(scores, axis=-1)
    
    mask = max_scores > score_thresh
    if not np.any(mask):
        return [], [], []
        
    y_indices, x_indices = np.where(mask)
    filtered_scores = max_scores[mask]
    filtered_class_ids = class_ids[mask]
    filtered_boxes = box_tensor[mask].reshape(-1, 4, 16)
    
    exp_boxes = np.exp(filtered_boxes - np.max(filtered_boxes, axis=-1, keepdims=True))
    softmax_boxes = exp_boxes / np.sum(exp_boxes, axis=-1, keepdims=True)
    distances = np.sum(softmax_boxes * np.arange(16, dtype=np.float32), axis=-1)
    
    cx, cy = x_indices, y_indices
    x1 = (cx + 0.5 - distances[:, 0]) * stride
    y1 = (cy + 0.5 - distances[:, 1]) * stride
    x2 = (cx + 0.5 + distances[:, 2]) * stride
    y2 = (cy + 0.5 + distances[:, 3]) * stride
    
    w = x2 - x1
    h = y2 - y1
    decoded_boxes = np.stack([x1, y1, w, h], axis=-1)
    
    return decoded_boxes.tolist(), filtered_scores.tolist(), filtered_class_ids.tolist()

def letterbox_image(image, expected_size=(800, 800), color=(114, 114, 114)):
    ih, iw = image.shape[0:2]
    ew, eh = expected_size
    scale = min(ew / iw, eh / ih)
    nw = int(iw * scale)
    nh = int(ih * scale)

    image_resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    padded_image = np.full((eh, ew, 3), color, dtype=np.uint8)

    dw = (ew - nw) // 2
    dh = (eh - nh) // 2
    padded_image[dh:nh+dh, dw:nw+dw, :] = image_resized
    
    return padded_image, scale, dw, dh

# 🌐 [새로 추가] 백그라운드에서 서버로 데이터를 보내는 함수 (카메라 렉 방지용)
def send_to_server_async(material_type, is_clean):
    def _send():
        try:
            # 1. 쓰레기를 감지하자마자 서버에 "지금 화면에 떠 있는 핀 번호 뭐야?" 물어보기
            pin_res = requests.get("aws밴앤드 서버주소/api/kiosk/current_pin", timeout=2)
            active_pin = pin_res.json().get("pin")
            
            # 2. 누군가 로그인해서 핀 번호가 떠 있을 때만(None이 아닐 때만) 전송!
            if active_pin:
                payload = {"pin": active_pin, "material": material_type, "clean": is_clean}
                response = requests.post(SERVER_URL, json=payload, timeout=2)
                
                if response.status_code == 200:
                    print(f"\n☁️ [AWS 전송] 핀 번호 '{active_pin}'로 경험치 업데이트 완료!")
                else:
                    print(f"\n☁️ [AWS 실패] {response.json()}")
            else:
                # 화면에 핀 번호가 없다면 = 아직 아무도 로그인을 안 했다면
                print("\n⚠️ [AWS 보류] 현재 로그인한 사용자가 없어 데이터를 전송하지 않습니다.")
                
        except Exception as e:
            print("\n☁️ [AWS 통신 오류]", e)
            
    # 백그라운드 전송 실행
    threading.Thread(target=_send, daemon=True).start()

def main():
    # 💡 ROS 2 노드 초기화 및 퍼블리셔 생성
    rclpy.init()
    node = rclpy.create_node('hailo_vision_node')
    publisher = node.create_publisher(String, '/trash_class', 10)
    last_publish_time = 0

    hef_path = 'best_800_GPU.hef'
    print(f"📦 [Initialization] Loading custom HEF binary ({hef_path}) into Hailo-8 core...")
    hef = HEF(hef_path)

    configure_params = None
    if hasattr(hef, 'get_configure_params'):
        configure_params = hef.get_configure_params()
    elif hasattr(hef, 'create_configure_params'):
        configure_params = hef.create_configure_params()
    elif hasattr(hailo_platform, 'ConfigureParams') and hasattr(hailo_platform.ConfigureParams, 'get_default_config'):
        configure_params = hailo_platform.ConfigureParams.get_default_config(hef)

    with VDevice() as target:
        if configure_params is not None:
            network_group = target.configure(hef, configure_params)[0]
        else:
            network_group = target.configure(hef)[0]
            
        input_vstreams_params = hailo_platform.InputVStreamParams.make_from_network_group(network_group, format_type=FormatType.UINT8)
        output_vstreams_params = hailo_platform.OutputVStreamParams.make_from_network_group(network_group, format_type=FormatType.FLOAT32)
        input_layer_name = list(input_vstreams_params.keys())[0]

        print("🔌 [Camera] Launching Asynchronous Camera Stream...")
        cam = ThreadedCamera("tcp://127.0.0.1:5000") 
        tracker = ObjectTracker() # 🔥 트래커 생성

        print("🚀 [Pipeline] Real-time synchronized pipeline active!")
        print("💡 [Tip] Press 'q' to close, Press 'c' to capture CLEAR image for Fine-Tuning.")
        sys.stdout.flush()
        
        prev_time = 0

        with network_group.activate():
            while True:
                ret, frame = cam.read()
                if not ret or frame is None:
                    continue
                
                clean_frame_for_capture = frame.copy()
                orig_h, orig_w = frame.shape[:2]
                
                # 왜곡 방지 레터박스 적용
                resized_frame, scale, dw, dh = letterbox_image(frame, (800, 800))
                
                rgb_frame = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
                input_tensor = np.array(np.expand_dims(rgb_frame, axis=0), dtype=np.uint8, order='C')
                input_data = {input_layer_name: input_tensor}
                
                with InferVStreams(network_group, input_vstreams_params, output_vstreams_params) as infer_pipeline:
                    npu_outputs = infer_pipeline.infer(input_data)
                
                stride_mappings = {
                    8:  {"box": None, "score": None},
                    16: {"box": None, "score": None},
                    32: {"box": None, "score": None}
                }
                
                for key, tensor in npu_outputs.items():
                    shape = tensor.shape
                    grid_size = shape[1] 
                    channels = shape[-1] 
                    
                    if grid_size == 100:
                        stride = 8
                    elif grid_size == 50:
                        stride = 16
                    elif grid_size == 25:
                        stride = 32
                    else:
                        continue
                        
                    if channels == 64:
                        stride_mappings[stride]["box"] = tensor
                    else:
                        stride_mappings[stride]["score"] = tensor
                
                all_boxes, all_scores, all_class_ids = [], [], []
                for stride, layers in stride_mappings.items():
                    if layers["box"] is None or layers["score"] is None:
                        continue
                    boxes, scores, class_ids = decode_yolov8_stride(layers["box"], layers["score"], stride, score_thresh=CONFIDENCE_THRESHOLD)
                    all_boxes.extend(boxes)
                    all_scores.extend(scores)
                    all_class_ids.extend(class_ids)
                    
                indices = cv2.dnn.NMSBoxes(all_boxes, all_scores, score_threshold=CONFIDENCE_THRESHOLD, nms_threshold=0.45)
                
                current_detections = []
                
                if len(indices) > 0:
                    for i in indices.flatten():
                        class_id = all_class_ids[i]
                        
                        if class_id not in TARGET_CLASSES:
                            continue

                        x_top_left, y_top_left, box_w, box_h = all_boxes[i]
                        
                        x1 = int((x_top_left - dw) / scale)
                        y1 = int((y_top_left - dh) / scale)
                        x2 = int((x_top_left + box_w - dw) / scale)
                        y2 = int((y_top_left + box_h - dh) / scale)
                        
                        x1 = max(0, min(x1, orig_w - 1))
                        y1 = max(0, min(y1, orig_h - 1))
                        x2 = max(0, min(x2, orig_w - 1))
                        y2 = max(0, min(y2, orig_h - 1))
                        
                        score = all_scores[i]
                        current_detections.append([x1, y1, x2, y2, score, class_id])

                # 🔥 [핵심] 트래커 업데이트
                active_tracks = tracker.update(current_detections)
                
                # 추적된 결과(다수결)를 화면에 그리기
                for track in active_tracks:
                    if track['missed'] > 0:
                        continue # 이번 프레임에서 놓친 물체는 그리지 않음
                        
                    x1, y1, x2, y2 = map(int, track['box'])
                    
                    # 최근 5프레임 중 가장 많이 나온 클래스 선택 (다수결)
                    classes = list(track['class_history'])
                    most_common_class = Counter(classes).most_common(1)[0][0]
                    class_name = CLASS_NAMES[most_common_class]
                    
                    # 스코어는 최근 5프레임 평균
                    avg_score = sum(track['score_history']) / len(track['score_history'])
                    
                    # 🔥 [ROS 2 통신] plastic_clean 감지 시 3초에 한 번만 메세지 전송 (모터 과부하 방지)
                    current_time = time.time()
                    if class_name in ['plastic_clean', 'can_clean', 'glass_clean', 'paper_clean', 'vinyl_clean'] and avg_score > 0.65:
                        if (current_time - last_publish_time) > 3.0:
                            msg = String()
                            msg.data = class_name
                            publisher.publish(msg)
                            print(f"\n📡 [ROS 2] 모터 제어 노드로 '{class_name}' 방송 완료!\n")

                            # 2. 💡 [새로 추가] AWS 서버로 API 전송
                            # class_name(예: 'plastic_clean')을 '_' 기준으로 분리
                            material_type, condition = class_name.split('_')
                            is_clean = (condition == 'clean') # clean이면 True, 아니면 False
                            
                            # 백그라운드로 전송 시작!
                            send_to_server_async(material_type, is_clean)

                            last_publish_time = current_time
                    
                    label = f"{class_name}: {avg_score:.2f} (Vote)"
                    color = TARGET_COLORS[most_common_class]
                    
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3) # 안정화된 박스는 더 굵게 표시
                    cv2.putText(frame, label, (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

                current_time = time.time()
                fps = 1 / (current_time - prev_time) if (current_time - prev_time) > 0 else 0
                prev_time = current_time
                
                cv2.putText(frame, f"Hailo-8 FPS: {int(fps)} (Anti-Distort + Vote)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(frame, "[Press 'c' to Capture, 'q' to Quit]", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
                
                cv2.imshow("Hailo-8 Contamination Detection v2", frame)
                
                # 💡 ROS 2 노드의 이벤트 처리를 위해 아주 짧게 spin 실행
                rclpy.spin_once(node, timeout_sec=0.001)
                
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('c'):
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"capture_{timestamp}.jpg"
                    cv2.imwrite(filename, clean_frame_for_capture)
                    print(f"📸 찰칵! 깨끗한 원본 화면이 {filename} 으로 저장되었습니다.")

        cam.release()
        cv2.destroyAllWindows()
        node.destroy_node()       # ROS 2 노드 종료
        rclpy.shutdown()          # ROS 2 종료
        print("🧹 Hardware stream and pipeline closed cleanly.")

if __name__ == '__main__':
    main()
