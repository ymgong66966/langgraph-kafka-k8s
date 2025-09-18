#!/usr/bin/env python3
"""
Quick script to get the exact role ARN for withcare-dev profile
"""
import boto3
import json

def get_withcare_dev_role():
    try:
        session = boto3.Session(profile_name="withcare-dev", region_name="us-east-2")
        sts_client = session.client('sts')
        identity = sts_client.get_caller_identity()
        
        arn = identity['Arn']
        print(f"withcare-dev profile ARN: {arn}")
        
        # Extract role name from ARN
        if 'assumed-role' in arn:
            role_name = arn.split('/')[-2]
            role_arn = f"arn:aws:iam::{identity['Account']}:role/{role_name}"
            print(f"Role ARN for Kubernetes: {role_arn}")
            return role_arn
        else:
            print("Not using an assumed role")
            return None
            
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    get_withcare_dev_role()
