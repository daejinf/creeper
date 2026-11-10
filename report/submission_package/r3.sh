#!/bin/bash

ip link set r3-eth0 down
ip link set r3-eth0 name eth0
ip link set eth0 up

ip link set r3-eth1 down
ip link set r3-eth1 name eth1
ip link set eth1 up

ip link set r3-eth2 down
ip link set r3-eth2 name eth2
ip link set eth2 up

ip link set r3-eth3 down
ip link set r3-eth3 name eth3
ip link set eth3 up

ip link set r3-eth4 down
ip link set r3-eth4 name eth4
ip link set eth4 up

ifconfig eth0 192.168.23.2 netmask 255.255.255.252 up
ifconfig eth1 192.168.34.1 netmask 255.255.255.252 up
ifconfig eth2 40.0.0.1 netmask 255.255.255.0 up
ifconfig eth3 40.0.1.1 netmask 255.255.255.0 up
ifconfig eth4 192.168.35.1 netmask 255.255.255.252 up

sysctl -w net.ipv4.ip_forward=1

route add -net 60.0.0.0 netmask 255.255.255.0 gw 192.168.35.2
route add default gw 192.168.23.1
