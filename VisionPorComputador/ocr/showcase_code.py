import multiprocessing as mp
import queue
import time
import traceback
import cv2
import numpy as np
from PIL import Image
import customtkinter as ctk

# =====================================================================
# CONFIGURATION
# =====================================================================
AREA_OF_INTEREST = (80, 740, 620, 1400)  # (y1, y2, x1, x2) as used in MTGOCR
ROTATE_IN_OCR = True                     # Must match rotate=True inside MTGOCR

CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080
SCAN_TIMEOUT_SECONDS = 90.0


def draw_roi_overlay(
    rgb_img: np.ndarray,
    aoi: tuple = AREA_OF_INTEREST,
    is_rotated: bool = ROTATE_IN_OCR
) -> np.ndarray:
    """
    Accurately maps MTGOCR's 180° rotation and YYXX slice coordinates 
    back onto the live camera frame so the bounding box aligns perfectly.
    """
    overlay = rgb_img.copy()
    h, w, _ = overlay.shape
    y1, y2, x1, x2 = aoi

    if is_rotated:
        draw_x1 = w - x2
        draw_x2 = w - x1
        draw_y1 = h - y2
        draw_y2 = h - y1
    else:
        draw_x1, draw_x2 = x1, x2
        draw_y1, draw_y2 = y1, y2

    x1_c, x2_c = max(0, min(w - 1, draw_x1)), max(0, min(w - 1, draw_x2))
    y1_c, y2_c = max(0, min(h - 1, draw_y1)), max(0, min(h - 1, draw_y2))

    box_color = (0, 230, 118)
    corner_color = (59, 130, 246)

    cv2.rectangle(overlay, (x1_c, y1_c), (x2_c, y2_c), box_color, 3)

    box_w = max(1, x2_c - x1_c)
    box_h = max(1, y2_c - y1_c)
    line_len = max(12, min(int(box_w * 0.12), int(box_h * 0.12)))
    thickness = 5

    # Corner brackets
    cv2.line(overlay, (x1_c, y1_c), (x1_c + line_len, y1_c), corner_color, thickness)
    cv2.line(overlay, (x1_c, y1_c), (x1_c, y1_c + line_len), corner_color, thickness)
    cv2.line(overlay, (x2_c, y1_c), (x2_c - line_len, y1_c), corner_color, thickness)
    cv2.line(overlay, (x2_c, y1_c), (x2_c, y1_c + line_len), corner_color, thickness)
    cv2.line(overlay, (x1_c, y2_c), (x1_c + line_len, y2_c), corner_color, thickness)
    cv2.line(overlay, (x1_c, y2_c), (x1_c, y2_c - line_len), corner_color, thickness)
    cv2.line(overlay, (x2_c, y2_c), (x2_c - line_len, y2_c), corner_color, thickness)
    cv2.line(overlay, (x2_c, y2_c), (x2_c, y2_c - line_len), corner_color, thickness)

    label_text = "MTGOCR Target Zone (Rotated 180°)"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.6
    font_thick = 2
    (tw, th), _ = cv2.getTextSize(label_text, font, font_scale, font_thick)

    if y1_c - th - 12 < 0:
        tag_y1, tag_y2 = y1_c, y1_c + th + 12
        text_y = y1_c + th + 4
    else:
        tag_y1, tag_y2 = y1_c - th - 12, y1_c
        text_y = y1_c - 6

    tag_x2 = min(w - 1, x1_c + tw + 14)
    cv2.rectangle(overlay, (x1_c, tag_y1), (tag_x2, tag_y2), box_color, -1)
    cv2.putText(
        overlay, label_text, (x1_c + 7, text_y),
        font, font_scale, (15, 23, 42), font_thick, cv2.LINE_AA
    )

    return overlay


# =====================================================================
# 1. MULTIPROCESSING WORKER
# =====================================================================
def ocr_worker(input_queue: mp.Queue, output_queue: mp.Queue, aoi: tuple):
    try:
        from MTGOCR import MTGOCR
        ocr_machine = MTGOCR(
            rotate=ROTATE_IN_OCR,
            area_of_interest=aoi,
            scale=1,
            noise_removal_threshold_area=100,
            holes_removal_threshold_area=10,

        )
    except Exception as e:
        output_queue.put({
            "status": "error",
            "message": f"MTGOCR initialization failed: {str(e)}"
        })
        return

    while True:
        job = input_queue.get()
        img, finish = job if job is not None else (None, 'Auto')
        if img is None:
            break

        # Discard stale frames if multiple captures were fired
        while not input_queue.empty():
            try:
                next_img = input_queue.get_nowait()
                if next_img is None:
                    return
                img, finish = next_img
            except queue.Empty:
                break

        try:
            scanned_text, card_name, is_special_card, card_image, scanned_results = (
                ocr_machine.recognize_card(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), finish=finish)
            )

            annotated_orig = draw_roi_overlay(img, aoi=aoi, is_rotated=ROTATE_IN_OCR)

            output_queue.put({
                "status": "success",
                "orig_img": annotated_orig,
                "scanned_text": scanned_text + "\n\nConsulta: " + (ocr_machine.last_query or "No se pudo leer edición/número") + "\n" + ocr_machine.last_warning,
                "card_name": card_name,
                "is_special_card": is_special_card,
                "card_image": card_image,
                "scanned_results": scanned_results
            })
        except Exception as e:
            traceback.print_exc()
            output_queue.put({
                "status": "error",
                "message": str(e)
            })


# =====================================================================
# 2. MODERN GUI APPLICATION
# =====================================================================
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class CardScannerApp(ctk.CTk):
    def __init__(
        self,
        input_queue: mp.Queue,
        output_queue: mp.Queue,
        worker_proc: mp.Process,
        aoi: tuple,
        camera_index: int = 0
    ):
        super().__init__()
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.worker_proc = worker_proc
        self.aoi = aoi
        self.camera_index = camera_index

        self.title("MTG Card OCR Scanner Pro")
        self.geometry("1280x820")
        self.minsize(1050, 700)

        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            print(f"[Warning] Camera index {self.camera_index} not found. Falling back to index 0.")
            self.cap = cv2.VideoCapture(0)

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[Info] Camera initialized at {actual_w}x{actual_h} resolution.")

        self.current_frame_rgb = None
        self.is_processing = False
        self.scan_start_time = 0.0

        self._build_ui(actual_w, actual_h)

        self.bind("<KeyPress-p>", self.trigger_capture)
        self.bind("<KeyPress-P>", self.trigger_capture)

        self.update_webcam()
        self.check_ocr_results()

    def _build_ui(self, cam_w: int, cam_h: int):
        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=7)
        self.grid_rowconfigure(0, weight=1)

        # Left Panel
        self.left_panel = ctk.CTkFrame(self, corner_radius=12)
        self.left_panel.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        self.left_panel.grid_rowconfigure(1, weight=1)
        self.left_panel.grid_columnconfigure(0, weight=1)

        self.top_bar = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="ew")
        self.top_bar.grid_columnconfigure(0, weight=1)

        self.header_label = ctk.CTkLabel(
            self.top_bar,
            text=f"LIVE ({cam_w}x{cam_h}) - Press 'P'",
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.header_label.grid(row=0, column=0, sticky="w")

        self.show_live_roi_var = ctk.BooleanVar(value=True)
        self.roi_switch = ctk.CTkSwitch(
            self.top_bar,
            text="Show AOI Box",
            variable=self.show_live_roi_var,
            font=ctk.CTkFont(size=12)
        )
        self.roi_switch.grid(row=0, column=1, sticky="e")

        self.webcam_label = ctk.CTkLabel(
            self.left_panel, text="Starting Camera...", corner_radius=8, fg_color="#1a1a1a"
        )
        self.webcam_label.grid(row=1, column=0, padx=15, pady=10, sticky="nsew")

        self.controls_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.controls_frame.grid(row=2, column=0, padx=15, pady=5, sticky="ew")
        self.controls_frame.grid_columnconfigure(0, weight=1)

        self.capture_btn = ctk.CTkButton(
            self.controls_frame,
            text="Capture & Scan (P)",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40,
            command=self.trigger_capture
        )
        self.capture_btn.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.status_badge = ctk.CTkLabel(
            self.controls_frame,
            text="READY",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2fa572",
            text_color="white",
            corner_radius=6,
            width=110,
            height=40
        )
        self.status_badge.grid(row=0, column=1, sticky="e")

        self.info_frame = ctk.CTkFrame(self.left_panel, corner_radius=10)
        self.info_frame.grid(row=3, column=0, padx=15, pady=15, sticky="ew")
        self.info_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.info_frame, text="Card Name:", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, padx=12, pady=(12, 4), sticky="w")
        self.card_name_val = ctk.CTkLabel(
            self.info_frame, text="—", font=ctk.CTkFont(size=15), anchor="w"
        )
        self.card_name_val.grid(row=0, column=1, padx=12, pady=(12, 4), sticky="ew")

        ctk.CTkLabel(
            self.info_frame, text="Acabado:", font=ctk.CTkFont(weight="bold")
        ).grid(row=1, column=0, padx=12, pady=4, sticky="w")
        self.special_val = ctk.CTkLabel(
            self.info_frame, text="—", font=ctk.CTkFont(size=15), anchor="w"
        )
        self.special_val.grid(row=1, column=1, padx=12, pady=4, sticky="ew")

        ctk.CTkLabel(
            self.info_frame, text="Scanned Text:", font=ctk.CTkFont(weight="bold")
        ).grid(row=2, column=0, columnspan=2, padx=12, pady=(8, 4), sticky="w")
        self.scanned_text_box = ctk.CTkTextbox(self.info_frame, height=110, wrap="word")
        self.scanned_text_box.grid(row=3, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="ew")
        self.scanned_text_box.configure(state="disabled")

        self.finish_var = ctk.StringVar(value="Auto")
        ctk.CTkOptionMenu(self.info_frame, values=["Auto", "Foil", "Nonfoil"],
                          variable=self.finish_var).grid(row=4, column=0, padx=12, pady=8)
        ctk.CTkLabel(self.info_frame, text="Auto: estrella = foil; revisar si falla").grid(row=4, column=1)

        # Right Panel
        self.right_panel = ctk.CTkFrame(self, corner_radius=12)
        self.right_panel.grid(row=0, column=1, padx=(0, 15), pady=15, sticky="nsew")
        self.right_panel.grid_columnconfigure((0, 1), weight=1)
        self.right_panel.grid_rowconfigure((1, 3), weight=1)

        ctk.CTkLabel(
            self.right_panel, text="1. Captured Frame + AOI Box", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, padx=10, pady=(12, 2))
        self.img_orig_label = ctk.CTkLabel(
            self.right_panel, text="No capture yet", fg_color="#1a1a1a", corner_radius=8
        )
        self.img_orig_label.grid(row=1, column=0, padx=10, pady=8, sticky="nsew")

        ctk.CTkLabel(
            self.right_panel, text="2. Scanned Results (Grayscale)", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=1, padx=10, pady=(12, 2))
        self.img_scan_label = ctk.CTkLabel(
            self.right_panel, text="No capture yet", fg_color="#1a1a1a", corner_radius=8
        )
        self.img_scan_label.grid(row=1, column=1, padx=10, pady=8, sticky="nsew")

        ctk.CTkLabel(
            self.right_panel, text="3. Recognized Card Image", font=ctk.CTkFont(weight="bold")
        ).grid(row=2, column=0, columnspan=2, padx=10, pady=(12, 2))
        self.img_card_label = ctk.CTkLabel(
            self.right_panel, text="No capture yet", fg_color="#1a1a1a", corner_radius=8
        )
        self.img_card_label.grid(row=3, column=0, columnspan=2, padx=10, pady=(8, 15), sticky="nsew")

    def update_webcam(self):
        ret, frame = self.cap.read()
        if ret:
            self.current_frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if self.show_live_roi_var.get():
                preview_frame = draw_roi_overlay(self.current_frame_rgb, aoi=self.aoi, is_rotated=ROTATE_IN_OCR)
            else:
                preview_frame = self.current_frame_rgb

            self._display_image(preview_frame, self.webcam_label, max_size=(480, 340))

        else:
            self.webcam_label.configure(image='', text='No se pudo leer la cámara. Revisa conexión e índice.')
        self.after(20, self.update_webcam)

    def trigger_capture(self, event=None):
        if self.current_frame_rgb is None or self.is_processing:
            return

        if not self.worker_proc.is_alive():
            self.status_badge.configure(text="WORKER DEAD", fg_color="#dc2626")
            print("[Error] Worker process terminated unexpectedly.")
            return

        self.is_processing = True
        self.scan_start_time = time.time()
        self.status_badge.configure(text="SCANNING...", fg_color="#d97706")
        self.capture_btn.configure(state="disabled")

        # Drain any backlog in input queue before posting current frame
        while not self.input_queue.empty():
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                break

        self.card_name_val.configure(text='—')
        self._display_image(None, self.img_card_label)
        self.input_queue.put((self.current_frame_rgb.copy(), self.finish_var.get()))

    def check_ocr_results(self):
        try:
            # Check worker health
            if self.is_processing and not self.worker_proc.is_alive():
                print("[Error] OCR worker process died during scanning.")
                self.status_badge.configure(text="CRASHED", fg_color="#dc2626")
                self.is_processing = False
                self.capture_btn.configure(state="normal")
                return

            # Watchdog timeout check
            if self.is_processing and (time.time() - self.scan_start_time > SCAN_TIMEOUT_SECONDS):
                self.worker_proc.terminate()
                self.card_name_val.configure(text="La lectura tardó demasiado. Cierra y vuelve a abrir la aplicación.", wraplength=400)
                self.status_badge.configure(text="TIMEOUT", fg_color="#dc2626")
                self.is_processing = False
                self.capture_btn.configure(state="disabled")
                return

            # Check for results
            result = None
            try:
                result = self.output_queue.get_nowait()
            except queue.Empty:
                pass

            if result is not None:
                try:
                    if result["status"] == "success":
                        card_name = result["card_name"]
                        has_match = bool(card_name and str(card_name).strip())

                        self.card_name_val.configure(
                            text=str(card_name) if has_match else "Carta no encontrada",
                            text_color="#ffffff" if has_match else "#f87171"
                        )

                        is_spec = result["is_special_card"]
                        self.special_val.configure(
                            text="Foil (inferido si Auto)" if is_spec else "Nonfoil (inferido si Auto)",
                            text_color="#f59e0b" if is_spec else "#9ca3af"
                        )

                        self.scanned_text_box.configure(state="normal")
                        self.scanned_text_box.delete("1.0", "end")
                        scanned_str = str(result["scanned_text"]).strip()
                        self.scanned_text_box.insert("1.0", scanned_str if scanned_str else "[No text detected]")
                        self.scanned_text_box.configure(state="disabled")

                        self._display_image(result["orig_img"], self.img_orig_label, max_size=(340, 260))
                        self._display_image(
                            result["scanned_results"], self.img_scan_label,
                            max_size=(340, 260), is_gray=True, empty_text="No Scanned Result"
                        )
                        self._display_image(
                            result["card_image"], self.img_card_label,
                            max_size=(450, 300), empty_text="Carta no encontrada en Scryfall"
                        )

                        if has_match:
                            self.status_badge.configure(text="MATCHED", fg_color="#2fa572")
                        else:
                            self.status_badge.configure(text="NO MATCH", fg_color="#6b7280")
                    else:
                        self.card_name_val.configure(text=result["message"], wraplength=400)
                        self._display_image(None, self.img_card_label)
                        self.status_badge.configure(text="ERROR", fg_color="#dc2626")
                except Exception as render_err:
                    traceback.print_exc()
                    self.status_badge.configure(text="UI ERROR", fg_color="#dc2626")
                finally:
                    self.is_processing = False
                    self.capture_btn.configure(state="normal")

        except Exception as loop_err:
            traceback.print_exc()
        finally:
            # Guaranteed reschedule so the polling loop never terminates
            self.after(50, self.check_ocr_results)

    def _display_image(
        self, np_img: np.ndarray, label_widget: ctk.CTkLabel,
        max_size=(350, 250), is_gray=False, empty_text="No Image"
    ):
        """Safely renders NumPy arrays or clears labels using empty string image='' to prevent Tkinter TclError."""
        if np_img is None or not isinstance(np_img, np.ndarray) or np_img.size == 0 or np_img.ndim < 2:
            label_widget.configure(image="", text=empty_text)
            label_widget.image = None
            return

        try:
            if np_img.dtype != np.uint8:
                if np_img.max() <= 1.0:
                    np_img = (np_img * 255).astype(np.uint8)
                else:
                    np_img = np_img.astype(np.uint8)

            if is_gray or np_img.ndim == 2:
                pil_img = Image.fromarray(np_img, mode="L")
            else:
                pil_img = Image.fromarray(np_img, mode="RGB")

            pil_img.thumbnail(max_size, Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
            label_widget.configure(image=ctk_img, text="")
            label_widget.image = ctk_img
        except Exception:
            label_widget.configure(image="", text="Render Error")
            label_widget.image = None

    def on_closing(self):
        self.input_queue.put(None)
        if self.cap.isOpened():
            self.cap.release()
        self.destroy()


def main(camera_index=0):
    mp.freeze_support()

    # Unbounded queues prevent OS pipe buffer deadlocks with multi-megabyte NumPy arrays
    in_q = mp.Queue()
    out_q = mp.Queue()

    worker_proc = mp.Process(target=ocr_worker, args=(in_q, out_q, AREA_OF_INTEREST), daemon=True)
    worker_proc.start()

    app = CardScannerApp(
        input_queue=in_q,
        output_queue=out_q,
        worker_proc=worker_proc,
        aoi=AREA_OF_INTEREST,
        camera_index=camera_index
    )
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()

    worker_proc.join(timeout=2)

if __name__ == '__main__':
    main()





