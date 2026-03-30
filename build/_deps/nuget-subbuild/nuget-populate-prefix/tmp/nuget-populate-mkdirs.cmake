# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "C:/progetti/PrezzoRadar/build/_deps/nuget-src")
  file(MAKE_DIRECTORY "C:/progetti/PrezzoRadar/build/_deps/nuget-src")
endif()
file(MAKE_DIRECTORY
  "C:/progetti/PrezzoRadar/build/_deps/nuget-build"
  "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix"
  "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix/tmp"
  "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix/src/nuget-populate-stamp"
  "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix/src"
  "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix/src/nuget-populate-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix/src/nuget-populate-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "C:/progetti/PrezzoRadar/build/_deps/nuget-subbuild/nuget-populate-prefix/src/nuget-populate-stamp${cfgdir}") # cfgdir has leading slash
endif()
