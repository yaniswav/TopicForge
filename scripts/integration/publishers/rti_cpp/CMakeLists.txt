# C++ (Modern C++ API) / RTI Connext participant of the TopicForge multi-vendor
# demo.
#
# Pattern taken from rticommunity/rticonnextdds-examples (master, commit
# cacbfb90a2e7772a01218e9663763742d589852a):
#   examples/connext_dds/build_systems/cmake/CMakeLists.txt (find_package +
#   connextdds_rtiddsgen_run), and
#   examples/connext_dds/asynchronous_publication/c++11/CMakeLists.txt, whose
#   connextdds_add_example(LANG "C++11") helper expands to the same calls with
#   LANG C++11 and the RTIConnextDDS::cpp2_api target.
#
# Difference from those examples: the module path comes from the Connext
# installation (<NDDSHOME>/resource/cmake, where FindRTIConnextDDS.cmake and
# ConnextDdsCodegen.cmake ship) instead of an examples repository checkout.
#
# Configure:  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON
# NDDSHOME must point at the Connext installation (or pass -DCONNEXTDDS_DIR=...).
cmake_minimum_required(VERSION 3.11)
project(rti_cpp CXX)

# FindRTIConnextDDS does not set the language standard; the Modern C++ API
# needs C++11 at least.
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED ENV{NDDSHOME} AND NOT CONNEXTDDS_DIR)
    message(FATAL_ERROR
        "Set the NDDSHOME environment variable (or -DCONNEXTDDS_DIR=...) to "
        "your RTI Connext Professional 7.x installation directory.")
endif()
if(DEFINED ENV{NDDSHOME})
    list(APPEND CMAKE_MODULE_PATH "$ENV{NDDSHOME}/resource/cmake")
endif()
if(CONNEXTDDS_DIR)
    list(APPEND CMAKE_MODULE_PATH "${CONNEXTDDS_DIR}/resource/cmake")
endif()

# Shared Connext libraries: the program then needs the Connext lib directory on
# the library path at run time (see README.md).
set(BUILD_SHARED_LIBS ON CACHE BOOL "Link the shared Connext libraries")

find_package(RTIConnextDDS
    "7.0.0"
    REQUIRED
    COMPONENTS
        core
)

include(ConnextDdsCodegen)
connextdds_rtiddsgen_run(
    IDL_FILE
        "${CMAKE_CURRENT_SOURCE_DIR}/Heartbeat.idl"
    OUTPUT_DIRECTORY
        "${CMAKE_CURRENT_BINARY_DIR}/src"
    LANG C++11
)

add_executable(rti_cpp
    "${CMAKE_CURRENT_SOURCE_DIR}/rti_cpp.cxx"
    ${Heartbeat_CXX11_GENERATED_SOURCES}
)

target_link_libraries(rti_cpp
    PRIVATE
        RTIConnextDDS::cpp2_api
)

target_include_directories(rti_cpp
    PRIVATE
        "${CMAKE_CURRENT_BINARY_DIR}/src"
)
