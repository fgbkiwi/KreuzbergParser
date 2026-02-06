"""
Custom TrOCR Handwriting Detector
This is the ONLY custom OCR code needed - Kreuzberg handles everything else
"""
import logging
from typing import Dict, Optional
import numpy as np

try:
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    from PIL import Image
    import torch
    TROCR_AVAILABLE = True
except ImportError:
    TROCR_AVAILABLE = False

from config import Config
from utils.gpu_detector import gpu_detector

logger = logging.getLogger(__name__)


class HandwritingDetector:
    """
    Custom TrOCR processor for handwriting detection
    Only needed when Kreuzberg's standard OCR isn't sufficient
    """
    
    def __init__(self, config: Config = None):
        """
        Initialize TrOCR handwriting detector
        
        Args:
            config: Configuration object
        """
        self.config = config or Config()
        self.processor = None
        self.model = None
        self.device = 'cuda' if gpu_detector.cuda_available else 'cpu'
        
        if not TROCR_AVAILABLE:
            logger.warning(
                "TrOCR dependencies not available. "
                "Install with: pip install transformers torch pillow"
            )
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"TrOCR Handwriting Detector initialized on {self.device}")
    
    def load_model(self):
        """Lazy load TrOCR model (on-demand)"""
        if not self.enabled:
            logger.warning("TrOCR not available, cannot load model")
            return False
        
        if self.model is not None:
            return True  # Already loaded
        
        try:
            logger.info("Loading TrOCR model...")
            self.processor = TrOCRProcessor.from_pretrained(
                self.config.TROCR_MODEL
            )
            self.model = VisionEncoderDecoderModel.from_pretrained(
                self.config.TROCR_MODEL
            )
            self.model.to(self.device)
            logger.info("TrOCR model loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load TrOCR model: {e}")
            self.enabled = False
            return False
    
    def detect_handwriting_in_image(self, image_path: str) -> Dict:
        """
        Detect and extract handwriting from image
        
        Args:
            image_path: Path to image file
        
        Returns:
            Dict with: {
                'has_handwriting': bool,
                'text': str,
                'confidence': float
            }
        """
        if not self.enabled:
            return {
                'has_handwriting': False,
                'text': '',
                'confidence': 0.0,
                'error': 'TrOCR not available'
            }
        
        # Load model if needed
        if not self.load_model():
            return {
                'has_handwriting': False,
                'text': '',
                'confidence': 0.0,
                'error': 'Failed to load TrOCR model'
            }
        
        try:
            # Load image
            image = Image.open(image_path).convert('RGB')
            
            # Process with TrOCR
            text = self._extract_text_from_image(image)
            
            # Simple heuristic: if TrOCR extracted meaningful text, consider it handwriting
            has_handwriting = len(text.strip()) > 5
            confidence = self._estimate_confidence(text)
            
            return {
                'has_handwriting': has_handwriting,
                'text': text,
                'confidence': confidence
            }
            
        except Exception as e:
            logger.error(f"Error detecting handwriting: {e}")
            return {
                'has_handwriting': False,
                'text': '',
                'confidence': 0.0,
                'error': str(e)
            }
    
    def _extract_text_from_image(self, image: Image.Image) -> str:
        """
        Extract text from PIL Image using TrOCR
        
        Args:
            image: PIL Image
        
        Returns:
            Extracted text
        """
        try:
            # Prepare image
            pixel_values = self.processor(
                images=image,
                return_tensors="pt"
            ).pixel_values
            
            pixel_values = pixel_values.to(self.device)
            
            # Generate text
            generated_ids = self.model.generate(
                pixel_values,
                max_length=self.config.TROCR_MAX_LENGTH
            )
            
            # Decode
            generated_text = self.processor.batch_decode(
                generated_ids,
                skip_special_tokens=True
            )[0]
            
            return generated_text.strip()
            
        except Exception as e:
            logger.error(f"TrOCR extraction failed: {e}")
            return ""
    
    def _estimate_confidence(self, text: str) -> float:
        """
        Estimate confidence in handwriting detection
        
        Args:
            text: Extracted text
        
        Returns:
            Confidence score (0-1)
        """
        # Simple heuristic based on text length and characteristics
        if not text:
            return 0.0
        
        # Longer text = more confident
        length_score = min(len(text) / 100, 1.0)
        
        # Presence of common words increases confidence
        common_words = ['de', 'para', 'em', 'com', 'por', 'o', 'a']
        word_score = sum(1 for word in common_words if word in text.lower()) / len(common_words)
        
        # Combined score
        confidence = (length_score * 0.6) + (word_score * 0.4)
        
        return min(confidence, 1.0)
    
    def enhance_page_with_handwriting_detection(self, page_data: Dict, image_path: Optional[str] = None) -> Dict:
        """
        Enhance page data with handwriting detection
        
        Args:
            page_data: Page data dict from Kreuzberg
            image_path: Optional path to page image
        
        Returns:
            Enhanced page data with handwriting info
        """
        if not image_path or not self.enabled:
            page_data['handwriting_detected'] = False
            return page_data
        
        # Run handwriting detection
        hw_result = self.detect_handwriting_in_image(image_path)
        
        # Enhance page data
        page_data['handwriting_detected'] = hw_result['has_handwriting']
        page_data['handwriting_confidence'] = hw_result['confidence']
        
        # If handwriting found and confidence high, append to text
        if hw_result['has_handwriting'] and hw_result['confidence'] > self.config.HANDWRITING_CONFIDENCE_THRESHOLD:
            if hw_result['text']:
                page_data['text'] += f"\n\n[Texto manuscrito detectado]\n{hw_result['text']}"
                page_data['has_handwriting_text'] = True
        
        return page_data
    
    def cleanup(self):
        """Free GPU memory"""
        if self.model:
            del self.model
            del self.processor
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("TrOCR resources freed")
