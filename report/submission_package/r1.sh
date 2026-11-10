#!/bin/bash

ip link set r1-eth0 down
ip link set r1-eth0 name eth0
ip link set eth0 up

ip link set r1-eth1 down
ip link set r1-eth1 name eth1
ip link set eth1 up

ip link set r1-eth2 down
ip link set r1-eth2 name eth2
ip link set eth2 up

ifconfig eth0 10.0.0.1 netmask 255.255.255.0 up
ifconfig eth1 192.168.12.1 netmask 255.255.255.252 up
ifconfig eth2 192.168.14.1 netmask 255.255.255.252 up

sysctl -w net.ipv4.ip_forward=1

route add -net 60.0.0.0 netmask 255.255.255.0 gw 192.168.14.2
route add default gw 192.168.12.2
