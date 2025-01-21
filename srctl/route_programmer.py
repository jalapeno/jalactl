from pyroute2 import IPRoute
import vpp_papi
from abc import ABC, abstractmethod
import os
import ipaddress

class RouteProgrammer(ABC):
    @abstractmethod
    def program_route(self, destination_prefix, srv6_usid, **kwargs):
        pass

class LinuxRouteProgrammer(RouteProgrammer):
    def __init__(self):
        if os.geteuid() != 0:
            raise PermissionError("Root privileges required for route programming. Please run with sudo.")
        self.iproute = IPRoute()

    def _expand_srv6_usid(self, usid):
        """Expand SRv6 USID to full IPv6 address"""
        # Remove any trailing colons
        usid = usid.rstrip(':')
        
        # Split the USID into parts
        parts = usid.split(':')
        
        # Add zeros to make it a complete IPv6 address (8 parts)
        while len(parts) < 8:
            parts.append('0')
            
        return ':'.join(parts)

    def program_route(self, destination_prefix, srv6_usid, **kwargs):
        """Program Linux SRv6 route using pyroute2"""
        try:
            if not destination_prefix:
                raise ValueError("destination_prefix is required")
            if not kwargs.get('outbound_interface'):
                raise ValueError("outbound_interface is required")
            
            # Get table ID, default to main table (254)
            table_id = kwargs.get('table_id', 254)
            
            # Validate and normalize the destination prefix
            try:
                net = ipaddress.ip_network(destination_prefix)
                dst = {'dst': str(net)}
            except ValueError as e:
                raise ValueError(f"Invalid destination prefix: {e}")

            # Validate and normalize the SRv6 USID
            try:
                expanded_usid = self._expand_srv6_usid(srv6_usid)
                ipaddress.IPv6Address(expanded_usid)
            except ValueError as e:
                raise ValueError(f"Invalid SRv6 USID: {e}")
            
            # Get interface index
            if_index = self.iproute.link_lookup(ifname=kwargs.get('outbound_interface'))[0]
            
            # Create encap info
            encap = {'type': 'seg6',
                    'mode': 'encap',
                    'segs': [expanded_usid]}
            
            # Try to delete existing route first
            try:
                self.iproute.route('del', table=table_id, dst=str(net))
                print(f"Deleted existing route to {str(net)} in table {table_id}")
            except Exception as e:
                # Ignore errors if route doesn't exist
                pass
            
            print(f"Adding route with encap: {encap} to table {table_id}")
            
            # Add new route
            self.iproute.route('add',
                             table=table_id,
                             dst=str(net),
                             oif=if_index,
                             encap=encap)
            
            return True, f"Route to {destination_prefix} via {expanded_usid} programmed successfully in table {table_id}"
        except Exception as e:
            return False, f"Failed to program route: {str(e)}"
        
    def __del__(self):
        if hasattr(self, 'iproute'):
            self.iproute.close()

class VPPRouteProgrammer(RouteProgrammer):
    def __init__(self):
        try:
            from vpp_papi import VPPApiClient
            self.vpp = VPPApiClient()
            self.vpp.connect("srctl")
            
            # Get VPP version
            version = self.vpp.api.show_version()
            self.version = version.version
            print(f"Connected to VPP version: {self.version}")
            
        except Exception as e:
            raise RuntimeError(f"Failed to connect to VPP: {str(e)}")

    def _expand_srv6_usid(self, usid):
        """Expand SRv6 USID to full IPv6 address"""
        # Remove any trailing colons
        usid = usid.rstrip(':')
        
        # Split the USID into parts
        parts = usid.split(':')
        
        # Add zeros to make it a complete IPv6 address (8 parts)
        while len(parts) < 8:
            parts.append('0')
            
        return ':'.join(parts)

    def program_route(self, destination_prefix, srv6_usid, **kwargs):
        """Program VPP SRv6 route using vpp_papi"""
        try:
            bsid = kwargs.get('bsid')
            if not bsid:
                raise ValueError("BSID is required for VPP routes")

            # Get table ID, default to 0
            table_id = kwargs.get('table_id', 0)

            # Validate the destination prefix
            try:
                net = ipaddress.ip_network(destination_prefix)
            except ValueError as e:
                raise ValueError(f"Invalid destination prefix: {e}")

            # Validate and expand the SRv6 USID
            try:
                expanded_usid = self._expand_srv6_usid(srv6_usid)
                srv6_usid_addr = ipaddress.IPv6Address(expanded_usid).packed
            except ValueError as e:
                raise ValueError(f"Invalid SRv6 USID: {e}")

            # Convert BSID to binary format
            bsid_addr = ipaddress.IPv6Address(bsid).packed

            # Add SR policy using lower-level API
            sr_policy_add = {
                'bsid_addr': bsid_addr,
                'weight': 1,
                'is_encap': 1,
                'is_spray': 0,
                'fib_table': table_id,
                'sids': {
                    'num_sids': 1,
                    'sids': [srv6_usid_addr]
                }
            }
            
            print(f"Sending sr_policy_add: {sr_policy_add}")  # Debug print
            self.vpp.api.sr_policy_add(**sr_policy_add)

            # Add steering policy using lower-level API
            prefix_addr = ipaddress.IPv4Address(str(net.network_address)).packed if isinstance(net, ipaddress.IPv4Network) else ipaddress.IPv6Address(str(net.network_address)).packed
            
            sr_steering_add = {
                'is_del': 0,
                'bsid_addr': bsid_addr,
                'table_id': table_id,
                'prefix': {
                    'address': prefix_addr,
                    'len': net.prefixlen
                },
                'traffic_type': 3  # L3 traffic
            }
            
            print(f"Sending sr_steering_add_del: {sr_steering_add}")  # Debug print
            self.vpp.api.sr_steering_add_del(**sr_steering_add)
            
            return True, f"Route to {destination_prefix} via {expanded_usid} programmed successfully in table {table_id}"
        except Exception as e:
            return False, f"Failed to program route: {str(e)}"

    def __del__(self):
        if hasattr(self, 'vpp'):
            self.vpp.disconnect()

class RouteProgrammerFactory:
    @staticmethod
    def get_programmer(platform):
        if platform.lower() == 'linux':
            return LinuxRouteProgrammer()
        elif platform.lower() == 'vpp':
            return VPPRouteProgrammer()
        else:
            raise ValueError(f"Unsupported platform: {platform}") 