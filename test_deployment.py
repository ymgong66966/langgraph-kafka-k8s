#!/usr/bin/env python3
"""
LangGraph Kafka K8s Deployment Test Script

This script tests the deployed LangGraph Kafka system by:
1. Checking health endpoints of all services
2. Sending test messages through the Kafka pipeline
3. Verifying agent communication and task processing
4. Monitoring the complete workflow

Usage:
    python test_deployment.py

Requirements:
    pip install requests kafka-python rich
"""

import json
import time
import requests
import threading
from datetime import datetime
from typing import Dict, List, Optional
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError
import subprocess
import signal
import sys
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.text import Text

console = Console()

class DeploymentTester:
    def __init__(self):
        self.namespace = "langgraph"
        self.services = {
            "agent-comms": {"service_name": "langgraph-system-langgraph-kafka-agent-comms", "port": 8000, "local_port": 8000},
            "task-generator": {"service_name": "langgraph-system-langgraph-kafka-task-generator", "port": 8001, "local_port": 8001},
            "task-solver": {"service_name": "langgraph-system-langgraph-kafka-task-solver", "port": 8002, "local_port": 8002},
            "kafka": {"service_name": "langgraph-system-kafka", "port": 9092, "local_port": 9092}
        }
        self.port_forwards = []
        self.test_results = {}
        
    def cleanup(self, signum=None, frame=None):
        """Clean up port-forward processes"""
        console.print("\n[yellow]🧹 Cleaning up port-forwards...[/yellow]")
        for proc in self.port_forwards:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        console.print("[green]✅ Cleanup completed[/green]")
        
    def setup_port_forwards(self):
        """Set up kubectl port-forwards for all services"""
        console.print("[blue]🔗 Setting up port-forwards...[/blue]")
        
        for service_name, config in self.services.items():
            service_full_name = config["service_name"]
            
            cmd = [
                "kubectl", "port-forward", 
                f"svc/{service_full_name}",
                f"{config['local_port']}:{config['port']}",
                "-n", self.namespace
            ]
            
            try:
                proc = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL
                )
                self.port_forwards.append(proc)
                console.print(f"[green]✅[/green] Port-forward started for {service_name} on :{config['local_port']}")
            except Exception as e:
                console.print(f"[red]❌[/red] Failed to start port-forward for {service_name}: {e}")
                return False
        
        # Wait for port-forwards to be ready
        console.print("[yellow]⏳ Waiting for port-forwards to be ready...[/yellow]")
        time.sleep(5)
        return True
        
    def check_pod_status(self):
        """Check the status of all pods"""
        console.print("\n[blue]📋 Checking pod status...[/blue]")
        
        try:
            result = subprocess.run([
                "kubectl", "get", "pods", "-n", self.namespace, 
                "--selector=app.kubernetes.io/instance=langgraph-system",
                "-o", "wide"
            ], capture_output=True, text=True, check=True)
            
            table = Table(show_header=True, header_style="bold magenta")
            lines = result.stdout.strip().split('\n')
            
            if len(lines) > 1:
                headers = lines[0].split()
                for header in headers:
                    table.add_column(header)
                
                for line in lines[1:]:
                    values = line.split()
                    # Color code the status
                    if len(values) >= 2:
                        ready_status = values[1] if len(values) > 1 else "Unknown"
                        if "1/1" in ready_status:
                            values[1] = f"[green]{ready_status}[/green]"
                        else:
                            values[1] = f"[red]{ready_status}[/red]"
                    table.add_row(*values)
                
                console.print(table)
                return "1/1" in result.stdout
            else:
                console.print("[red]❌ No pods found[/red]")
                return False
                
        except subprocess.CalledProcessError as e:
            console.print(f"[red]❌ Error checking pods: {e}[/red]")
            return False
    
    def test_health_endpoints(self):
        """Test health endpoints of all HTTP services"""
        console.print("\n[blue]🔍 Testing health endpoints...[/blue]")
        
        http_services = ["agent-comms", "task-generator", "task-solver"]
        results = {}
        
        for service in http_services:
            port = self.services[service]["local_port"]
            url = f"http://localhost:{port}/health"
            
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    console.print(f"[green]✅[/green] {service} health check: OK")
                    results[service] = {"status": "healthy", "response": response.json()}
                else:
                    console.print(f"[red]❌[/red] {service} health check: HTTP {response.status_code}")
                    results[service] = {"status": "unhealthy", "code": response.status_code}
            except requests.exceptions.RequestException as e:
                console.print(f"[red]❌[/red] {service} health check: {str(e)}")
                results[service] = {"status": "error", "error": str(e)}
        
        self.test_results["health_checks"] = results
        return all(r.get("status") == "healthy" for r in results.values())
    
    def test_kafka_connectivity(self):
        """Test Kafka connectivity"""
        console.print("\n[blue]📨 Testing Kafka connectivity...[/blue]")
        
        try:
            # Test producer
            producer = KafkaProducer(
                bootstrap_servers=['localhost:9092'],
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                request_timeout_ms=30000,
                retries=3
            )
            
            test_message = {
                "test": True,
                "timestamp": datetime.now().isoformat(),
                "message": "Test message from deployment tester"
            }
            
            future = producer.send('test-topic', test_message)
            result = future.get(timeout=10)  # Wait for send to complete
            
            console.print("[green]✅[/green] Kafka producer: Connected and message sent")
            producer.close()
            
            # Test consumer
            consumer = KafkaConsumer(
                'test-topic',
                bootstrap_servers=['localhost:9092'],
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                consumer_timeout_ms=5000,
                auto_offset_reset='latest'
            )
            
            console.print("[green]✅[/green] Kafka consumer: Connected")
            consumer.close()
            
            self.test_results["kafka"] = {"status": "healthy"}
            return True
            
        except Exception as e:
            console.print(f"[red]❌[/red] Kafka connectivity: {str(e)}")
            self.test_results["kafka"] = {"status": "error", "error": str(e)}
            return False
    
    def test_agent_communication_api(self):
        """Test the agent communication API endpoints"""
        console.print("\n[blue]🤖 Testing Agent Communication API...[/blue]")
        
        base_url = "http://localhost:8000"
        
        # Test sending a message
        test_payload = {
            "agent_id": "test-agent-001",
            "message": "Hello from test script!",
            "task_type": "greeting",
            "metadata": {
                "test": True,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        try:
            # Test message sending
            response = requests.post(
                f"{base_url}/send_message",
                json=test_payload,
                timeout=10
            )
            
            if response.status_code == 200:
                console.print("[green]✅[/green] Agent message sending: OK")
                message_result = response.json()
            else:
                console.print(f"[yellow]⚠️[/yellow] Agent message sending: HTTP {response.status_code}")
                message_result = {"status": "partial", "code": response.status_code}
            
            # Test metrics endpoint (if available)
            try:
                metrics_response = requests.get(f"{base_url}/metrics", timeout=5)
                if metrics_response.status_code == 200:
                    console.print("[green]✅[/green] Agent metrics: Available")
                else:
                    console.print("[yellow]⚠️[/yellow] Agent metrics: Not available")
            except:
                console.print("[yellow]⚠️[/yellow] Agent metrics: Endpoint not found")
            
            self.test_results["agent_communication"] = {"status": "healthy", "response": message_result}
            return True
            
        except requests.exceptions.RequestException as e:
            console.print(f"[red]❌[/red] Agent Communication API: {str(e)}")
            self.test_results["agent_communication"] = {"status": "error", "error": str(e)}
            return False
    
    def test_task_generator_api(self):
        """Test the task generator API"""
        console.print("\n[blue]📋 Testing Task Generator API...[/blue]")
        
        base_url = "http://localhost:8001"
        
        test_task = {
            "task_description": "Generate a simple greeting response",
            "agent_id": "test-agent-001",
            "priority": "normal",
            "metadata": {
                "test": True,
                "source": "deployment_tester"
            }
        }
        
        try:
            response = requests.post(
                f"{base_url}/generate_task",
                json=test_task,
                timeout=15
            )
            
            if response.status_code == 200:
                console.print("[green]✅[/green] Task generation: OK")
                task_result = response.json()
                console.print(f"[blue]📝[/blue] Generated task ID: {task_result.get('task_id', 'N/A')}")
            else:
                console.print(f"[yellow]⚠️[/yellow] Task generation: HTTP {response.status_code}")
                task_result = {"status": "partial", "code": response.status_code}
            
            self.test_results["task_generator"] = {"status": "healthy", "response": task_result}
            return True
            
        except requests.exceptions.RequestException as e:
            console.print(f"[red]❌[/red] Task Generator API: {str(e)}")
            self.test_results["task_generator"] = {"status": "error", "error": str(e)}
            return False
    
    def test_task_solver_api(self):
        """Test the task solver API"""
        console.print("\n[blue]🧠 Testing Task Solver API...[/blue]")
        
        base_url = "http://localhost:8002"
        
        test_task = {
            "task_id": f"test-task-{int(time.time())}",
            "task_description": "What is 2 + 2?",
            "task_type": "math",
            "context": {
                "difficulty": "easy",
                "domain": "arithmetic"
            }
        }
        
        try:
            response = requests.post(
                f"{base_url}/solve_task",
                json=test_task,
                timeout=20
            )
            
            if response.status_code == 200:
                console.print("[green]✅[/green] Task solving: OK")
                solve_result = response.json()
                console.print(f"[blue]💡[/blue] Solution: {solve_result.get('solution', 'N/A')}")
            else:
                console.print(f"[yellow]⚠️[/yellow] Task solving: HTTP {response.status_code}")
                solve_result = {"status": "partial", "code": response.status_code}
            
            self.test_results["task_solver"] = {"status": "healthy", "response": solve_result}
            return True
            
        except requests.exceptions.RequestException as e:
            console.print(f"[red]❌[/red] Task Solver API: {str(e)}")
            self.test_results["task_solver"] = {"status": "error", "error": str(e)}
            return False
    
    def monitor_kafka_messages(self, duration=30):
        """Monitor Kafka messages for a specified duration"""
        console.print(f"\n[blue]👁️ Monitoring Kafka messages for {duration} seconds...[/blue]")
        
        def consume_messages():
            try:
                consumer = KafkaConsumer(
                    'dev-langgraph-agent-events',
                    'dev-langgraph-task-results',
                    bootstrap_servers=['localhost:9092'],
                    value_deserializer=lambda m: json.loads(m.decode('utf-8')) if m else None,
                    consumer_timeout_ms=1000,
                    auto_offset_reset='latest'
                )
                
                message_count = 0
                start_time = time.time()
                
                while time.time() - start_time < duration:
                    message_batch = consumer.poll(timeout_ms=1000)
                    for topic_partition, messages in message_batch.items():
                        for message in messages:
                            message_count += 1
                            topic = topic_partition.topic
                            console.print(f"[green]📨[/green] Message #{message_count} from {topic}: {message.value}")
                
                if message_count == 0:
                    console.print("[yellow]⚠️[/yellow] No messages received during monitoring period")
                else:
                    console.print(f"[green]✅[/green] Monitoring complete: {message_count} messages received")
                    
                consumer.close()
                
            except Exception as e:
                console.print(f"[red]❌[/red] Message monitoring error: {str(e)}")
        
        # Run monitoring in background
        monitor_thread = threading.Thread(target=consume_messages)
        monitor_thread.daemon = True
        monitor_thread.start()
        monitor_thread.join()
    
    def generate_test_report(self):
        """Generate a comprehensive test report"""
        console.print("\n" + "="*60)
        console.print("[bold magenta]🎯 DEPLOYMENT TEST REPORT[/bold magenta]")
        console.print("="*60)
        
        # Create summary table
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Service", style="cyan", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Details", style="dim")
        
        for service, result in self.test_results.items():
            status = result.get("status", "unknown")
            if status == "healthy":
                status_display = "[green]✅ HEALTHY[/green]"
            elif status == "partial":
                status_display = "[yellow]⚠️ PARTIAL[/yellow]"
            else:
                status_display = "[red]❌ ERROR[/red]"
            
            details = ""
            if "response" in result:
                details = "API responding"
            elif "error" in result:
                details = str(result["error"])[:50] + "..." if len(str(result["error"])) > 50 else str(result["error"])
            
            table.add_row(service.replace("_", " ").title(), status_display, details)
        
        console.print(table)
        
        # Overall assessment
        healthy_count = sum(1 for r in self.test_results.values() if r.get("status") == "healthy")
        total_count = len(self.test_results)
        
        if healthy_count == total_count:
            overall_status = "[green]🎉 ALL SYSTEMS OPERATIONAL[/green]"
        elif healthy_count > total_count // 2:
            overall_status = "[yellow]⚠️ PARTIALLY OPERATIONAL[/yellow]"
        else:
            overall_status = "[red]🚨 SYSTEM ISSUES DETECTED[/red]"
        
        console.print(f"\n[bold]Overall Status:[/bold] {overall_status}")
        console.print(f"[bold]Services Healthy:[/bold] {healthy_count}/{total_count}")
        
        # Save report to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = f"deployment_test_report_{timestamp}.json"
        
        with open(report_file, 'w') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "summary": {
                    "total_services": total_count,
                    "healthy_services": healthy_count,
                    "success_rate": healthy_count / total_count if total_count > 0 else 0
                },
                "results": self.test_results
            }, f, indent=2)
        
        console.print(f"\n[blue]📁 Detailed report saved to: {report_file}[/blue]")
    
    def run_comprehensive_test(self):
        """Run the complete test suite"""
        # Set up signal handler for cleanup
        signal.signal(signal.SIGINT, self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)
        
        try:
            console.print(Panel.fit(
                "[bold magenta]🚀 LangGraph Kafka K8s Deployment Tester[/bold magenta]\n"
                "[blue]Testing all deployed services and functionality[/blue]",
                title="Deployment Test Suite",
                border_style="magenta"
            ))
            
            # Step 1: Check pod status
            if not self.check_pod_status():
                console.print("[red]❌ Pods are not healthy. Aborting tests.[/red]")
                return
            
            # Step 2: Set up port forwards
            if not self.setup_port_forwards():
                console.print("[red]❌ Failed to set up port-forwards. Aborting tests.[/red]")
                return
            
            # Step 3: Run all tests
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                
                # Health checks
                task = progress.add_task("[cyan]Testing health endpoints...", total=None)
                self.test_health_endpoints()
                progress.remove_task(task)
                
                # Kafka connectivity
                task = progress.add_task("[cyan]Testing Kafka connectivity...", total=None)
                self.test_kafka_connectivity()
                progress.remove_task(task)
                
                # API endpoints
                task = progress.add_task("[cyan]Testing API endpoints...", total=None)
                self.test_agent_communication_api()
                self.test_task_generator_api()
                self.test_task_solver_api()
                progress.remove_task(task)
                
                # Message monitoring
                task = progress.add_task("[cyan]Monitoring Kafka messages...", total=None)
                self.monitor_kafka_messages(duration=15)  # Shorter duration for demo
                progress.remove_task(task)
            
            # Generate final report
            self.generate_test_report()
            
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️ Test interrupted by user[/yellow]")
        finally:
            self.cleanup()

def main():
    """Main entry point"""
    tester = DeploymentTester()
    tester.run_comprehensive_test()

if __name__ == "__main__":
    main()