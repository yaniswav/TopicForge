cmake_minimum_required(VERSION 3.22)

project(fast_publisher_demo LANGUAGES CXX)

# Fast DDS 3.x and Fast CDR 2.x. Point CMAKE_PREFIX_PATH at the install prefix
# when they are not in a default location.
find_package(fastcdr 2 REQUIRED)
find_package(fastdds 3 REQUIRED)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Single-config generators (Linux) leave the binary in build/. Multi-config
# generators (MSVC) put it in build/Release/ on their own.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

add_executable(fast_publisher src/main.cpp)
target_link_libraries(fast_publisher PRIVATE fastdds fastcdr)
