r"""
Parse additional arguments along with the setup.py arguments such as install, build, distribute, sdist, etc.


Usage:

  python setup.py install <additional_flags>..<additional_flags> <additional_arg>=<value>..<additional_arg>=<value>

  export CXX=<C++ compiler>; python setup.py install <additional_flags>..<additional_flags> <additional_arg>=<value>..<additional_arg>=<value>


Examples:

  python setup.py install --force_cuda --cuda_home=/usr/local/cuda
  export CXX=g++7; python setup.py install --force_cuda --cuda_home=/usr/local/cuda


Additional flags:

  --cpu_only: Force building only a CPU version. However, if
      torch.cuda.is_available() is False, it will default to CPU_ONLY.

  --force_cuda: If torch.cuda.is_available() is false, but you have a working
      nvcc, compile cuda files. --force_cuda will supercede --cpu_only.


Additional arguments:

  --blas=<value> : type of blas library to use for CPU matrix multiplications.
      Options: [openblas, mkl, atlas, blas]. By default, it will use the first
      numpy blas library it finds.

  --cuda_home=<value> : a directory that contains <value>/bin/nvcc and
      <value>/lib64/libcudart.so. By default, use
      `torch.utils.cpp_extension._find_cuda_home()`.

  --blas_include_dirs=<comma_separated_values> : additional include dirs. Only
      activated when --blas=<value> is set.

  --blas_library_dirs=<comma_separated_values> : additional library dirs. Only
      activated when --blas=<value> is set.
"""
import sys

if sys.version_info < (3, 6):
    sys.stdout.write(
        "Minkowski Engine requires Python 3.6 or higher. Please use anaconda https://www.anaconda.com/distribution/ for an isolated python environment.\n"
    )
    sys.exit(1)

try:
    import torch
except ImportError:
    raise ImportError("Pytorch not found. Please install pytorch first.")

import codecs
import os
import re
import subprocess
from sys import argv, platform
from setuptools import setup
from torch.utils.cpp_extension import CppExtension, CUDAExtension, BuildExtension
from pathlib import Path

from distutils.sysconfig import get_python_inc

if platform == "win32":
    raise ImportError("Windows is currently not supported.")
elif platform == "darwin":
    # Set the distutils to use clang instead of g++ for valid std
    os.environ["CC"] = "/usr/local/opt/llvm/bin/clang"
    os.environ["CXX"] = "/usr/local/opt/llvm/bin/clang"

here = os.path.abspath(os.path.dirname(__file__))


def read(*parts):
    with codecs.open(os.path.join(here, *parts), "r") as fp:
        return fp.read()


def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


def _argparse(pattern, argv, is_flag=True):
    if is_flag:
        found = pattern in argv
        if found:
            argv.remove(pattern)
        return found, argv
    else:
        arr = [arg for arg in argv if pattern in arg]
        if len(arr) == 0:  # not found
            return False, argv
        else:
            assert "=" in arr[0], f"{arr[0]} requires a value."
            argv.remove(arr[0])
            return arr[0].split("=")[1], argv


# For cpu only build
CPU_ONLY, argv = _argparse("--cpu_only", argv)
FORCE_CUDA, argv = _argparse("--force_cuda", argv)
CPU_ONLY = CPU_ONLY or not torch.cuda.is_available()
if FORCE_CUDA:
    CPU_ONLY = False

# args with return value
CUDA_HOME, argv = _argparse("--cuda_home", argv, False)
BLAS, argv = _argparse("--blas", argv, False)
BLAS_INCLUDE_DIRS, argv = _argparse("--blas_include_dirs", argv, False)
BLAS_LIBRARY_DIRS, argv = _argparse("--blas_library_dirs", argv, False)

Extension = CUDAExtension
extra_compile_args = ["-Wno-deprecated-declarations"]
extra_link_args = []
libraries = []

# extra_compile_args+=['-g']  # Uncomment for debugging
if CPU_ONLY:
    print("--------------------------------")
    print("| WARNING: CPU_ONLY build set  |")
    print("--------------------------------")
    Extension = CppExtension
else:
    print("--------------------------------")
    print("| CUDA compilation set         |")
    print("--------------------------------")
    # system python installation
    libraries.append("cusparse")

if not (CUDA_HOME is False):  # False when not set, str otherwise
    print(f"Using CUDA_HOME={CUDA_HOME}")

BLAS_LIST = ["openblas", "mkl", "atlas", "blas"]
if not (BLAS is False):  # False only when not set, str otherwise
    assert BLAS in BLAS_LIST
    libraries.append(BLAS)
    if not (BLAS_INCLUDE_DIRS is False):
        compile_args += [f"BLAS_INCLUDE_DIRS={BLAS_INCLUDE_DIRS}"]
    if not (BLAS_LIBRARY_DIRS is False):
        extra_link_args += [f"-Wl,-rpath,{BLAS_LIBRARY_DIRS}"]
else:
    # find the default BLAS library
    import numpy.distutils.system_info as sysinfo

    # Search blas in this order
    for blas in BLAS_LIST:
        if "libraries" in sysinfo.get_info(blas):
            BLAS = blas
            libraries += sysinfo.get_info(blas)["libraries"]
            break
    else:
        # BLAS not found
        raise ImportError(
            ' \
\nBLAS not found from numpy.distutils.system_info.get_info. \
\nPlease specify BLAS with: python setup.py install --blas=openblas" \
\nfor more information, please visit https://github.com/NVIDIA/MinkowskiEngine/wiki/Installation'
        )

print(f"\nUsing BLAS={BLAS}")

# The Ninja cannot compile the files that have the same name with different
# extensions correctly and uses the nvcc/CXX based on the extension. Import a
# .cpp file to the corresponding .cu file to force the nvcc compilation.
SOURCE_SETS = {
    "cpu": [
        CppExtension,
        [
            "math_functions.cpp",
            "coordinate_map_manager.cpp",
            "convolution_cpu.cpp",
            "convolution_transpose_cpu.cpp",
        ],
        ["pybind/minkowski.cpp"],
        ["-DCPU_ONLY"],
    ],
    "gpu": [
        CUDAExtension,
        [
            "math_functions.cpp",
            "coordinate_map_manager.cu",
            "convolution_gpu.cu",
            "coordinate_map_gpu.cu",
            "convolution_kernel.cu",
            "convolution_transpose_gpu.cu",
            "spmm.cu",
            "gpu.cu",
        ],
        ["pybind/minkowski.cu"],
        [],
    ],
}

debug, argv = _argparse("--debug", argv)

USE_NINJA = os.getenv("USE_NINJA") == "0"
HERE = Path(os.path.dirname(__file__)).absolute()
SRC_PATH = HERE / "src"

try:
    CXX = os.environ["CXX"]
except:
    CXX = "g++"

if sys.platform == "win32":
    vc_version = os.getenv("VCToolsVersion", "")
    if vc_version.startswith("14.16."):
        CXX_FLAGS = ["/sdl"]
    else:
        CXX_FLAGS = ["/sdl", "/permissive-"]
else:
    CXX_FLAGS = ["-fopenmp"]

if "darwin" in platform:
    CXX_FLAGS += ["-stdlib=libc++"]

NVCC_FLAGS = [f"-ccbin={CXX}", "--extended-lambda"]

if debug:
    CXX_FLAGS += ["-g", "-DDEBUG"]
    NVCC_FLAGS += ["-g", "-DDEBUG"]
else:
    CXX_FLAGS += ["-O3"]
    NVCC_FLAGS += ["-O3"]

test_target = "cpu" if CPU_ONLY else "gpu"

Extension = SOURCE_SETS[test_target][0]
SRC_FILES = SOURCE_SETS[test_target][1]
BIND_FILES = SOURCE_SETS[test_target][2]
ARGS = SOURCE_SETS[test_target][3]
CXX_FLAGS += ARGS
NVCC_FLAGS += ARGS

ext_modules = [
    Extension(
        name="MinkowskiEngineBackend._C",
        # ["type_test.cpp", "],
        sources=[*[str(SRC_PATH / src_file) for src_file in SRC_FILES], *BIND_FILES],
        extra_compile_args={"cxx": CXX_FLAGS, "nvcc": NVCC_FLAGS,},
        # library_dirs=[str(OBJ_DIR), str(ME_OBJ_DIR)],
        libraries=libraries,
    ),
]

# Python interface
setup(
    name="MinkowskiEngine",
    version=find_version("MinkowskiEngine", "__init__.py"),
    install_requires=["torch", "numpy"],
    packages=["MinkowskiEngine", "MinkowskiEngine.utils", "MinkowskiEngine.modules"],
    package_dir={"MinkowskiEngine": "./MinkowskiEngine"},
    ext_modules=ext_modules,
    include_dirs=[
        str(SRC_PATH),
        str(SRC_PATH / "3rdparty"),
        # os.path.join(CUDA_HOME, "include"),
    ],
    cmdclass={"build_ext": BuildExtension},
    author="Christopher Choy",
    author_email="chrischoy@ai.stanford.edu",
    description="a convolutional neural network library for sparse tensors",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/NVIDIA/MinkowskiEngine",
    keywords=[
        "pytorch",
        "Minkowski Engine",
        "Sparse Tensor",
        "Convolutional Neural Networks",
        "3D Vision",
        "Deep Learning",
    ],
    zip_safe=False,
    classifiers=[
        # https://pypi.org/classifiers/
        "Environment :: Console",
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: Other Audience",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: C++",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Multimedia :: Graphics",
        "Topic :: Scientific/Engineering",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Scientific/Engineering :: Mathematics",
        "Topic :: Scientific/Engineering :: Physics",
        "Topic :: Scientific/Engineering :: Visualization",
    ],
    python_requires=">=3.6",
)
