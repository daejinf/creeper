#!/bin/bash

ip link set r4-eth0 down
ip link set r4-eth0 name eth0
ip link set eth0 up

ip link set r4-eth1 down
ip link set r4-eth1 name eth1
ip link set eth1 up

ip link set r4-eth2 down
ip link set r4-eth2 name eth2
ip link set eth2 up

ifconfig eth0 192.168.34.2 netmask 255.255.255.252 up
ifconfig eth1 192.168.14.2 netmask 255.255.255.252 up
ifconfig eth2 192.168.45.1 netmask 255.255.255.252 up

sysctl -w net.ipv4.ip_forward=1

route add -net 40.0.0.0 netmask 255.255.254.0 gw 192.168.34.1
route add -net 60.0.0.0 netmask 255.255.255.0 gw 192.168.45.2
route add default gw 192.168.14.1
