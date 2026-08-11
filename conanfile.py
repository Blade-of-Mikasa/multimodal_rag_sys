from conan import ConanFile


class MultimodalRagDependencies(ConanFile):
    """Pinned C++ dependencies for the Python/C++ process boundary."""

    settings = "os", "compiler", "build_type", "arch"
    generators = "CMakeDeps", "CMakeToolchain", "VirtualBuildEnv"

    def requirements(self) -> None:
        self.requires("grpc/1.82.0")

    def build_requirements(self) -> None:
        self.tool_requires("cmake/4.4.0")

    def configure(self) -> None:
        self.options["grpc"].shared = False
        self.options["grpc"].codegen = True
        self.options["grpc"].cpp_plugin = True
