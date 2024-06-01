import os
import subprocess
import sys

from .settings import PITCH_EXTRACTION_OPTIONS
from ..lib import BASE_MODELS_DIR
from ..lib.model_utils import load_hubert
from ..lib.utils import get_file_hash, get_filenames, get_hash, get_optimal_torch_device
from .utils import model_downloader
import torch
import folder_paths
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
from ..vc_infer_pipeline import get_vc
from .settings.downloader import RVC_DOWNLOAD_LINK, RVC_MODELS, download_file
from ..lib.audio import SUPPORTED_AUDIO, audio_to_bytes, load_input_audio
from ..config import config

input_path = folder_paths.get_input_directory()
temp_path = folder_paths.get_temp_directory()

model_ids = [
    'openai/whisper-large-v3',
    'openai/whisper-large-v2',
    'openai/whisper-large',
    'openai/whisper-medium',
    'openai/whisper-small',
    'openai/whisper-base',
    'openai/whisper-tiny',
    'openai/whisper-medium.en',
    'openai/whisper-small.en',
    'openai/whisper-base.en',
    'openai/whisper-tiny.en',
]

languages = ['en', 'fr', 'es']

spacy_model_map = {
    "en": "en_core_web_md",
    "fr": "fr_core_news_md",
    "es": "es_core_news_md"
}

class LoadPitchExtractionParams:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'f0_method': (PITCH_EXTRACTION_OPTIONS,{"default": "rmvpe"}),
                "f0_autotune": ("BOOLEAN",),
                "index_rate": ("FLOAT",{
                    "default": .75, 
                    "min": 0., #Minimum value
                    "max": 1., #Maximum value
                    "step": .01, #Slider's step
                    "display": "slider"
                }),
                "resample_sr": ([0,16000,32000,40000,44100,48000],{"default": 0}),
                "rms_mix_rate": ("FLOAT",{
                    "default": 0.25, 
                    "min": 0., #Minimum value
                    "max": 1., #Maximum value
                    "step": .01, #Slider's step
                    "display": "slider"
                }),
                "protect": ("FLOAT",{
                    "default": 0.25, 
                    "min": 0., #Minimum value
                    "max": .5, #Maximum value
                    "step": .01, #Slider's step
                    "display": "slider"
                })
            },
        }

    RETURN_TYPES = ('PITCH_EXTRACTION', )
    RETURN_NAMES = ('pitch_extraction_params', )

    CATEGORY = "🌺RVC-Studio/loaders"

    FUNCTION = 'load_params'

    def load_params(self, **params):
        if "rmvpe" in params.get("f0_method",""): model_downloader("rmvpe.pt")
        return (params,)

class LoadHubertModel:
    @classmethod
    def INPUT_TYPES(cls):
        model_list = ["hubert_base.pt"] + get_filenames(root=BASE_MODELS_DIR,folder="*",exts=["pt"],format_func=os.path.basename)
        return {
            'required': {
                'model': (model_list,{"default": "hubert_base.pt"}),
            },
        }

    RETURN_TYPES = ('HUBERT_MODEL', )
    RETURN_NAMES = ('hubert_model', )

    CATEGORY = "🌺RVC-Studio/loaders"

    FUNCTION = 'load_model'

    def load_model(self, model):
        model_path = model_downloader(model)
        hubert_model = lambda:load_hubert(model_path,config=config)
        return (hubert_model,)
    
    @classmethod
    def IS_CHANGED(cls, model):
        return get_hash(model)

class LoadRVCModelNode:

    @classmethod
    def INPUT_TYPES(cls):
        model_list = RVC_MODELS + get_filenames(root=BASE_MODELS_DIR,folder="RVC",exts=["pth"],format_func=lambda x: f"RVC/{os.path.basename(x)}")
        model_list = list(set(model_list)) # dedupe
        return {
            'required': {
                'model': (model_list,{"default": "RVC/Sayano.pth"}),
            },
        }

    RETURN_TYPES = ('RVC_MODEL', 'STRING')
    RETURN_NAMES = ('model', 'model_name')

    CATEGORY = "🌺RVC-Studio/loaders"

    FUNCTION = 'load_model'

    @classmethod
    def IS_CHANGED(cls, model):
        return get_hash(model)

    def load_model(self, model):
        try:
            filename = os.path.basename(model)
            subfolder = os.path.dirname(model)
            model_path = os.path.join(BASE_MODELS_DIR,subfolder,filename)

            if not os.path.isfile(model_path):
                download_link = f"{RVC_DOWNLOAD_LINK}{model}"
                params = model_path, download_link
                if download_file(params): print(f"successfully downloaded: {model_path}")
            model = lambda:get_vc(model_path)
            return (model,filename.split(".")[0])
        except Exception as e:
            print(f"Error in {self.__class__.__name__}: {e}")
            raise e
    
class LoadWhisperModelNode:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            'required': {
                'model_id': (model_ids,{"default": "openai/whisper-base.en"}),
            },
            "optional": {
                'language': (languages,{"default": "en"}),
                "max_new_tokens": ("INT", {"default": 128, "min": 16, "max": 1024, "display": "slider"}),
                "chunk_length_s": ("INT", {"default": 30, "min": 15, "max": 60, "display": "slider"}),
                "batch_size": ("INT", {"default": 16, "min": 1, "max": 128, "display": "slider"}),
            }
        }

    RETURN_TYPES = ('TRANSCRIPTION_MODEL', )
    RETURN_NAMES = ('model', )

    CATEGORY = "🌺RVC-Studio/loaders"

    FUNCTION = 'load_model'

    @classmethod
    def IS_CHANGED(cls, model):
        return get_hash(model)

    def load_model(self, model_id, language="en", max_new_tokens=128, chunk_length_s=12, batch_size=16):
        device = get_optimal_torch_device()
        torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id, torch_dtype=torch_dtype, low_cpu_mem_usage=True, use_safetensors=True
        )
        processor = AutoProcessor.from_pretrained(model_id)
        model.to(device)

        generate_kwargs = {}
        if model_id.endswith('.en'):
            if language != 'en':
                raise ValueError(f'Model {model_id} only supports English language')
        else:
            generate_kwargs['language'] = language

        def pipe():
            
            def get_spacy_model():
                import spacy
                model_name = spacy_model_map[language]
                model_path = os.path.join(BASE_MODELS_DIR,model_name)
                
                if not os.path.exists(model_path):
                    subprocess.call([sys.executable, "-m", "spacy", "download", model_name])
                    model = spacy.load(model_name)
                    model.to_disk(model_path)

                return spacy.load(model_path)
                
            return pipeline(
                'automatic-speech-recognition',
                model=model,
                tokenizer=processor.tokenizer,
                feature_extractor=processor.feature_extractor,
                max_new_tokens=max_new_tokens,
                chunk_length_s=chunk_length_s,
                batch_size=batch_size,
                return_timestamps=True,
                torch_dtype=torch_dtype,
                device=device,
                generate_kwargs=generate_kwargs,
            ),get_spacy_model
        return (pipe, )

class LoadAudio:
    @classmethod
    def INPUT_TYPES(cls):
        input_dir = input_path
        files = get_filenames(root=input_dir,exts=SUPPORTED_AUDIO,format_func=os.path.basename)
        
        return {
            "required": {
                "audio": (files,),
                "sr": (["None",16000,44100,48000],{"default": "None"}),
            }}

    CATEGORY = "🌺RVC-Studio/loaders"

    RETURN_TYPES = ("STRING","VHS_AUDIO")
    RETURN_NAMES = ("audio_name","vhs_audio")
    FUNCTION = "load_audio"

    def load_audio(self, audio, sr):
        audio_path = folder_paths.get_annotated_filepath(audio)
        widgetId = get_hash(audio_path)
        audio_name = os.path.basename(audio).split(".")[0]
        sr = None if sr=="None" else int(sr)
        audio = load_input_audio(audio_path,sr=sr)
        return {"ui": {"preview": [{"filename": audio_name, "type": "input", "widgetId": widgetId}]}, "result": (audio_name, lambda:audio_to_bytes(*audio))}

    @classmethod
    def IS_CHANGED(cls, audio):
        audio_path = folder_paths.get_annotated_filepath(audio)
        print(f"{audio_path=}")
        return get_file_hash(audio_path)