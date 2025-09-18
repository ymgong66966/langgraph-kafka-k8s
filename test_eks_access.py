#!/usr/bin/env python3
"""
Test script to verify EKS cluster access with withcare_dev profile only
"""
import boto3
import subprocess
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_withcare_dev_profile():
    """Test if withcare_dev profile can access EKS"""
    try:
        logger.info("Testing AWS profile: withcare_dev")
        session = boto3.Session(profile_name="withcare_dev", region_name="us-east-2")
        eks_client = session.client('eks')
        
        # Try to describe the cluster
        response = eks_client.describe_cluster(name='my-small-cluster')
        cluster_status = response['cluster']['status']
        logger.info(f"✅ withcare_dev profile: Cluster status = {cluster_status}")
        return True
        
    except Exception as e:
        logger.error(f"❌ withcare_dev profile failed: {e}")
        return False

def test_kubectl_access():
    """Test kubectl access to the cluster"""
    try:
        logger.info("Testing kubectl access...")
        result = subprocess.run(['kubectl', 'cluster-info'], 
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            logger.info("✅ kubectl access working")
            return True
        else:
            logger.error(f"❌ kubectl failed: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ kubectl test failed: {e}")
        return False

def update_kubeconfig():
    """Update kubeconfig with withcare_dev profile"""
    try:
        logger.info("Updating kubeconfig with withcare_dev profile")
        cmd = [
            'aws', 'eks', 'update-kubeconfig', 
            '--region', 'us-east-2', 
            '--name', 'my-small-cluster',
            '--profile', 'withcare_dev'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            logger.info("✅ Kubeconfig updated successfully")
            return True
        else:
            logger.error(f"❌ Kubeconfig update failed: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Kubeconfig update failed: {e}")
        return False

if __name__ == "__main__":
    print("=== EKS Access Test (withcare_dev only) ===\n")
    
    # Test withcare_dev profile
    if test_withcare_dev_profile():
        print("✅ withcare_dev profile can access EKS cluster")
        
        # Update kubeconfig
        if update_kubeconfig():
            # Test kubectl access
            if test_kubectl_access():
                print("\n✅ SUCCESS: EKS access working with withcare_dev profile")
                print("You can now deploy to Kubernetes!")
                print("\nNext steps:")
                print("1. Build and push Docker images")
                print("2. Deploy with: helm upgrade --install langgraph-kafka ./helm")
            else:
                print("\n⚠️  Kubeconfig updated but kubectl access still failing")
        else:
            print("\n❌ Failed to update kubeconfig")
    else:
        print("\n❌ withcare_dev profile cannot access EKS cluster")
        print("Check your AWS credentials and IAM permissions")
        print("Make sure withcare-mgmt profile has valid credentials")
