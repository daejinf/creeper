#!/bin/bash

ip link set r6-eth0 down
ip link set r6-eth0 name eth0
ip link set eth0 up

ip link set r6-eth1 down
ip link set r6-eth1 name eth1
ip link set eth1 up

ifconfig eth0 192.168.56.2 netmask 255.255.255.252 up
ifconfig eth1 60.0.0.1 netmask 255.255.255.0 up

sysctl -w net.ipv4.ip_forward=1

route add default gw 192.168.56.1
