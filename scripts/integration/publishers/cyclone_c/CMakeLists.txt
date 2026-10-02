cmake_minimum_required(VERSION 3.16)
project(cyclone_c LANGUAGES C)

# Locate the Cyclone DDS install through CYCLONEDDS_HOME when set. An explicit
# -DCMAKE_PREFIX_PATH (see build.sh / build.ps1) works as well.
if(DEFINED ENV{CYCLONEDDS_HOME})
  list(APPEND CMAKE_PREFIX_PATH "$ENV{CYCLONEDDS_HOME}")
endif()

if(NOT TARGET CycloneDDS::ddsc)
  find_package(CycloneDDS REQUIRED)
endif()

# idlc_generate compiles Heartbeat.idl into Heartbeat.h/.c and a library target
# (same pattern as examples/helloworld/CMakeLists.txt at tag 11.0.1).
idlc_generate(TARGET Heartbeat_lib FILES "Heartbeat.idl" WARNINGS no-implicit-extensibility)

add_executable(cyclone_c cyclone_c.c)
target_link_libraries(cyclone_c Heartbeat_lib CycloneDDS::ddsc)
