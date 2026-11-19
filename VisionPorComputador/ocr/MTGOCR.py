import cv2
import numpy as np
from skimage.morphology import remove_small_objects, remove_small_holes
from rapidocr_onnxruntime import RapidOCR
from scryfall import Scryfall
import io
from PIL import Image

from parser import MTGCardMatcher   # LOCAL MODULE 

class MTGOCR:
    
    def __init__(self,
                 rotate: bool = False,
                 area_of_interest: tuple[int, int, int, int] = (500, 780, 700, 1080),
                 scale: int = 1,
                 letters_size: int = 40,
                 k_sharpening_size: tuple = (51, 51),
                 k_median_size: int = 5,
                 noise_removal_threshold_area: int = 20,
                 holes_removal_threshold_area: int = 20,
                 star_crop_distances: tuple = (100,100),

                 ):

        self.rotate = rotate

        self.area_of_interest = area_of_interest
        self.scale = scale  
        self.letters_size = letters_size
        
        self.k_sharpening_size = k_sharpening_size # kernel for sharpening
        self.k_median_size = k_median_size
  
        self.noise_removal_threshold_area = noise_removal_threshold_area # adjust noise removal sensitivity based on area of detected contour 
        self.holes_removal_threshold_area = holes_removal_threshold_area 
                       
        self.star_crop_distances = star_crop_distances 

        self.ocr_engine = RapidOCR(use_cls=False)

        self.scryfall = Scryfall()
        self.matcher = MTGCardMatcher(self.scryfall.set_codes())

        self.tophat_structure = self.generate_tophat_structure()

    def generate_tophat_structure(self) -> np.ndarray:
        "for top hat filtering"
        
        tophat_dim = int(self.letters_size * 0.8) | 1 
        tophat_dim = max(31, tophat_dim)         

        return  cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (tophat_dim, tophat_dim)
        )

    def pre_process_image(self, image: np.ndarray) -> np.ndarray:
        """
        Input must be a BGR image (as read by cv2.imread).
        Outputs image ready for OCR and star recognition.
        """

        if self.rotate:
            image = cv2.rotate(image, cv2.ROTATE_180)

        zoomed_in_image = image[self.area_of_interest[0]:self.area_of_interest[1],
                                    self.area_of_interest[2]:self.area_of_interest[3]]

        if zoomed_in_image.size == 0:
            raise ValueError('Ajusta AREA_OF_INTEREST: la zona OCR queda fuera de la cámara.')
        gray_image = cv2.cvtColor(zoomed_in_image, cv2.COLOR_BGR2GRAY)

        if self.scale != 1:
            scaled_image = cv2.resize(gray_image, (gray_image.shape[1] * self.scale, gray_image.shape[0] * self.scale),
                                    interpolation=cv2.INTER_CUBIC)
            
        else:
            scaled_image = gray_image

        # top-hat filtering for lighting 
        
        top_hat = cv2.morphologyEx(scaled_image, cv2.MORPH_TOPHAT, self.tophat_structure)

        # filter noise
        
        median = cv2.medianBlur(top_hat, ksize=self.k_median_size)

        # apply sharpening, only neccesary after Interpolations
        
        if self.scale != 1:
        
            blurred = cv2.GaussianBlur(median, ksize=self.k_sharpening_size, sigmaX=0, sigmaY=0)
            unsharp_mask = cv2.subtract(median, blurred)
            sharpened = cv2.addWeighted(median, alpha=1.0, src2=unsharp_mask, beta=1.5, gamma=0)
            
        else:
            sharpened = median

        # Otsu
        
        _, otsu_thresh = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # cleaning noise by area (pixels blops)

        cleaned_blops = remove_small_objects(otsu_thresh.astype(bool), min_size=self.noise_removal_threshold_area)
        
        cleaned_holes = remove_small_holes(cleaned_blops, area_threshold=self.holes_removal_threshold_area)

        return cleaned_holes.astype(np.uint8) * 255  

    def recognize_card(self, image: np.ndarray, return_image: bool = True, finish: str = "Auto") -> tuple[str, str, bool, np.ndarray, np.ndarray]:
        
        """
        Input must be a BGR image (as read by cv2.imread).
        Outputs (OCR text, card name, inferred foil, Scryfall image, processed image).
        """

        pre_processed_image = self.pre_process_image(image)

        # OCR
        
        img_bgr = cv2.cvtColor(pre_processed_image, cv2.COLOR_GRAY2BGR)
        
        results, _ = self.ocr_engine(img_bgr, use_det=True, use_cls=False, use_rec=True)

        full_text = " ".join([item[1] for item in results]) if results else ""

        # Star recognition (★)
        
        is_special_card: bool = "★" in full_text

        number, rarity, editions = self.matcher.extract_candidates(full_text)
        foil = is_special_card if finish == "Auto" else finish == "Foil"
        self.last_query = ""
        self.last_warning = ""
        card_data = None
        if number and editions:
            self.last_query = self.scryfall.query(editions[0], str(int(number)), foil)
            card_data = self.scryfall.search(self.last_query)
        card_name = card_data["name"] if card_data else ""
        db_image = np.array([])
        if card_data:
            image_url = card_data.get("image_uris", {}).get("normal")
            if not image_url:
                faces = card_data.get("card_faces", [])
                image_url = faces[0].get("image_uris", {}).get("normal") if faces else None
            if image_url:
                try:
                    db_image = np.array(Image.open(io.BytesIO(self.scryfall.image(image_url))).convert("RGB"))
                except Exception:
                    self.last_warning = "Carta encontrada; no se pudo descargar su imagen."
        is_special_card = foil

        scanned_results = pre_processed_image if return_image else np.array([])  # what it saw

        return (full_text, card_name, is_special_card, db_image, scanned_results)


