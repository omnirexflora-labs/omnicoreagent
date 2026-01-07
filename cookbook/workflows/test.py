import os


def get_output_dir() -> str:
    """Returns the absolute path to the outputs directory."""
    output_dir = os.path.join(os.getcwd(), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


print(get_output_dir())
