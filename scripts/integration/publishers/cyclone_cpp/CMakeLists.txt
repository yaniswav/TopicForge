cmake_minimum_required(VERSION 3.16)
project(cyclone_cpp LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Locate the Cyclone DDS C and C++ installs through CYCLONEDDS_HOME when set.
# An explicit -DCMAKE_PREFIX_PATH (see build.sh / build.ps1) works as well.
if(DEFINED ENV{CYCLONEDDS_HOME})
  list(APPEND CMAKE_PREFIX_PATH "$ENV{CYCLONEDDS_HOME}")
endif()

if(NOT TARGET CycloneDDS-CXX::ddscxx)
  find_package(CycloneDDS-CXX REQUIRED)
endif()

# idlcxx_generate compiles Heartbeat.idl into Heartbeat.hpp/.cpp and a library
# target (same pattern as examples/helloworld/CMakeLists.txt at tag 11.0.1).
idlcxx_generate(TARGET Heartbeat_lib_cxx FILES Heartbeat.idl WARNINGS no-implicit-extensibility)

add_executable(cyclone_cpp cyclone_cpp.cpp)
target_link_libraries(cyclone_cpp CycloneDDS-CXX::ddscxx Heartbeat_lib_cxx)
