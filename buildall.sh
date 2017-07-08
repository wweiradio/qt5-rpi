#!/bin/bash
#
#  Build and package everything in 2 separate steps: native and cross
#
#  buildall < cross | native>
#
# TODO: We might want to build the debug version of QT5 to diagnose problems
#
# ./qt5-build purge --yes > $logfile 2>&1
# ./qt5-build compile qt5 cross debug --baptize --yes >> $logfile 2>&1
#
#

if [ "$1" == "cross" ]; then
    # takes about 1 hour on a 8 CPU 2GHz host
    echo  "Cross compilation of QT5 and Webengine"
    echo "Cross compilation of QT5 and Webengine\n\nRun purge "
#    ./qt5-build purge --yes
    echo "Run compile qt5 cross release\n+++++ "
    ./qt5-build compile qt5 cross release --baptize --yes
    echo "Run compile webengine  release\n++++ "
    ./qt5-build compile webengine release --yes
    echo "Run package qt5 "
    ./qt5-build package qt5

    echo "Run package webengine "
    ./qt5-build package webengine
    echo "Run package crosstools"
    ./qt5-build package cross-tools

    exit 0

else if [ "$1" == "native" ]; then
	 # takes about 1.5 hours on a 8 CPU 2GHz host
	 echo "Native compilation of QT5 core tools"
	 ./qt5-build compile qt5 native release --core-tools --yes
	 ./qt5-build package native-tools
	 exit 0

else if [ "$1" == "purge" ]; then

    echo "are you sure to purge all the contents???"
    echo "ctrl+c to change your idea."
    read n
    ./qt5-build purge --yes
    exit 0
else
	 echo "unrecognized build mode - please use native or cross"
	 exit 1
fi
