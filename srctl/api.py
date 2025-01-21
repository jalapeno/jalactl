import requests
from urllib.parse import urlencode
from .route_programmer import RouteProgrammerFactory

class JalapenoAPI:
    def __init__(self, config):
        self.config = config

    def apply(self, data):
        """Send configuration to Jalapeno API"""
        if not isinstance(data, dict):
            raise ValueError(f"Invalid configuration format: expected dict, got {type(data)}")
            
        if data.get('kind') == 'PathRequest':
            return self._handle_path_requests(data)
        else:
            raise ValueError(f"Unsupported resource kind: {data.get('kind')}")

    def _handle_path_requests(self, data):
        """Handle multiple PathRequest resources"""
        spec = data.get('spec', {})
        if not spec:
            raise ValueError("No spec found in configuration")
            
        results = []
        platform = spec.get('platform')
        if not platform:
            raise ValueError("Platform must be specified in spec")

        # Process default VRF/table routes
        default_vrf = spec.get('defaultVrf', {})
        results.extend(self._process_address_family(default_vrf.get('ipv4', {}), platform, 'ipv4', table_id=0))
        results.extend(self._process_address_family(default_vrf.get('ipv6', {}), platform, 'ipv6', table_id=0))

        # Process VRF/table-specific routes
        for vrf in spec.get('vrfs', []):
            table_id = vrf.get('tableId')
            if table_id is None:
                raise ValueError(f"tableId must be specified for VRF {vrf.get('name')}")
            
            results.extend(self._process_address_family(vrf.get('ipv4', {}), platform, 'ipv4', table_id=table_id))
            results.extend(self._process_address_family(vrf.get('ipv6', {}), platform, 'ipv6', table_id=table_id))
        
        return results

    def _process_address_family(self, af_config, platform, af_type, table_id):
        """Process routes for a specific address family"""
        results = []
        routes = af_config.get('routes', [])
        
        for route in routes:
            try:
                if not isinstance(route, dict):
                    raise ValueError(f"Invalid route format: {route}")
                
                # Add table_id to route configuration
                route['table_id'] = table_id
                
                # Build the base URL with optional metric
                base_url = f"{self.config.base_url}/api/v1/graphs/{route['graph']}/shortest_path"
                if 'metric' in route:
                    base_url = f"{base_url}/{route['metric']}"
                
                # Add query parameters
                params = {
                    'source': route['source'],
                    'destination': route['destination']
                }
                
                # Construct final URL with query parameters
                final_url = f"{base_url}?{urlencode(params)}"
                print(f"Making request to: {final_url}")  # Debug print
                
                # Make the request
                response = requests.get(final_url)
                if not response.ok:
                    error_msg = f"API request failed with status {response.status_code}: {response.text}"
                    print(f"API Error: {error_msg}")  # Debug print
                    raise requests.exceptions.RequestException(error_msg)
                
                response_data = response.json()
                print(f"API Response: {response_data}")  # Debug: Print full response
                
                srv6_data = response_data.get('srv6_data', {})
                print(f"SRv6 Data: {srv6_data}")  # Debug: Print SRv6 data
                
                srv6_usid = srv6_data.get('srv6_usid')
                print(f"SRv6 USID: {srv6_usid}")  # Debug: Print USID
                
                if not srv6_usid:
                    raise ValueError("No SRv6 USID received from API")
                
                try:
                    # Program the route
                    programmer = RouteProgrammerFactory.get_programmer(platform)
                    success, message = programmer.program_route(
                        destination_prefix=route.get('destination_prefix'),
                        srv6_usid=srv6_usid,
                        outbound_interface=route.get('outbound_interface'),
                        bsid=route.get('bsid'),
                        table_id=table_id
                    )
                    
                    if not success:
                        raise Exception(f"Route programming failed: {message}")
                    
                    results.append({
                        'name': route['name'],
                        'status': 'success',
                        'data': response_data,
                        'route_programming': message
                    })
                except Exception as e:
                    print(f"Route Programming Error: {str(e)}")  # Debug print
                    raise
                    
            except Exception as e:
                results.append({
                    'name': route.get('name', 'unknown'),
                    'status': 'error',
                    'error': f"Error: {str(e)}"
                })
        
        return results 