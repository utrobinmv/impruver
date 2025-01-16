import os
import subprocess
from typing import Dict, List

import fire
import yaml
from peft import AutoPeftModelForCausalLM

from impruver.utils import dynamic_import


def convert_gguf(
    config: str,
    llama_cpp_convert_script: str = "../llama.cpp/convert_hf_to_gguf.py",
    llama_cpp_quantize_bin: str = "../llama.cpp/build/bin/llama-quantize",
    quantizations: List[str] = ["q8_0", "q5_0", "q4_0", "q2_k"],
):
    """
    Convert a PyTorch model to a GGUF format, if provider mode is LoRA adapter,
    then merge if first, then convert. After that, quantize the model to list of required levels
    and save nearby a Modelfiles with configuration for importing models to Ollama.

    Args:
        config (str): Path to the configuration file
        llama_cpp_convert_script (str): Path to the llama.cpp convert script
        llama_cpp_quantize_bin (str): Path to the llama-quantize binary
        quantizations (List[str]): List of quantizations to use
    """

    #
    # Load configuration
    #

    if os.path.exists(config):
        config_path = config
    else:
        import recipes
        recipes_path = os.path.join(recipes.__path__[0])
        config_path = recipes_path + '/configs/' + config + '.yaml'

    # Read config
    with open(config_path, "r") as r:
        config = yaml.safe_load(r)

    # Output dir for merged and converted models
    output_dir = config["output_dir"]
    processing_dir = output_dir + "/processing"
    os.makedirs(processing_dir, exist_ok=True)
    gguf_dir = output_dir + "/gguf"
    os.makedirs(gguf_dir, exist_ok=True)

    # Class to work with Tokenizer
    tokenizer_class = "transformers.AutoTokenizer"
    if "class" in config["tokenizer"]:
        tokenizer_class = config["tokenizer"]["class"]

    #
    # Model preparation
    #

    # If LoRA is enabled, we assume we have a LoRA adapter in output_dir
    if config.get("lora", None):
        # Next need to save tokenizer into the same folder
        tokenizer_obj = dynamic_import(tokenizer_class)
        tokenizer = tokenizer_obj.from_pretrained(output_dir, trust_remote_code=True)
        tokenizer.save_pretrained(processing_dir)

        # This mean we need to merge an adapter into a model then save result to disk
        peft_model = AutoPeftModelForCausalLM.from_pretrained(
            output_dir,
            device_map={"": "cpu"},
            trust_remote_code=True
        ).to("cpu")
        model_processed = peft_model.merge_and_unload()
        model_processed.save_pretrained(processing_dir)
    else:
        processing_dir = output_dir

    #
    # Convert to GGUF format
    #

    gguf_model_path = os.path.join(gguf_dir, "model-fp16.gguf")

    print("Converting model to GGUF (FP16)...")
    convert_cmd = ["python", llama_cpp_convert_script, "--outtype", "f16", "--outfile", gguf_model_path, processing_dir]
    subprocess.run(convert_cmd, check=True)

    # Create the Modelfile.f16
    q_level = "f16"
    header_file_name = f"Modelfile.{q_level}"
    header_file_path = os.path.join(gguf_dir, header_file_name)
    with open(header_file_path, "w", encoding="utf-8") as hf:
        hf.write(f"FROM model-{q_level}.gguf\n")
        hf.write("PARAMETER temperature 1\n")
        hf.write("# PARAMETER num_ctx 4096\n")
        hf.write("# SYSTEM You are Super King, acting as a king.\n")

    #
    # Quantization loop
    #

    quant_list = [q for q in quantizations]
    for q_level in quant_list:
        print(f"Quantizing to {q_level}...")
        quant_out_path = os.path.join(gguf_dir, f"model-{q_level}.gguf")
        quant_cmd = [llama_cpp_quantize_bin, gguf_model_path, quant_out_path, q_level]
        subprocess.run(quant_cmd, check=True)

        # Create the Modelfile.qX_Y
        header_file_name = f"Modelfile.{q_level}"
        header_file_path = os.path.join(gguf_dir, header_file_name)
        with open(header_file_path, "w", encoding="utf-8") as hf:
            hf.write(f"FROM model-{q_level}.gguf\n")
            hf.write("PARAMETER temperature 1\n")
            hf.write("# PARAMETER num_ctx 4096\n")
            hf.write("# SYSTEM You are Super King, acting as a king.\n")


if __name__ == "__main__":
    fire.Fire(convert_gguf)
