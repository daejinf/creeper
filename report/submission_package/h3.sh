#!/bin/bash

ip link set h3-eth0 down
ip link set h3-eth0 name eth0
ip link set eth0 up

ifconfig eth0 30.0.0.11 netmask 255.255.255.0 up

route add default gw 30.0.0.1
