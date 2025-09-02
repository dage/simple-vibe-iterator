#!/usr/bin/env python3
"""
Integration test for OpenRouter model loading functionality.
Tests real API integration, data validation, filtering, and error handling.
"""

import asyncio
import sys
import os
from unittest.mock import patch

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.or_client import list_models, ModelInfo


class TestModelLoading:
    """Test suite for model loading and filtering functionality."""
    
    async def test_fetch_programming_models_success(self):
        """Test successful model fetching from OpenRouter API."""
        models = await list_models(force_refresh=True, limit=1000)
        
        # Basic validation
        assert isinstance(models, list), "Should return a list"
        assert len(models) > 0, "Should return at least one model"
        assert len(models) >= 100, "Should return hundreds of models"
        
        # Validate first model structure
        first_model = models[0]
        assert isinstance(first_model, ModelInfo), "Should return ModelInfo objects"
        assert first_model.id, "Model ID should not be empty"
        assert first_model.name, "Model name should not be empty"
        assert isinstance(first_model.has_text_input, bool), "has_text_input should be boolean"
        assert isinstance(first_model.has_image_input, bool), "has_image_input should be boolean"
        
        print(f"✅ Successfully loaded {len(models)} models")
        print(f"✅ First model: {first_model.id} - {first_model.name}")
    
    async def test_model_pricing_validation(self):
        """Test that model pricing is properly parsed and non-zero."""
        models = await list_models(limit=1000)
        
        models_with_pricing = [m for m in models if m.prompt_price > 0 or m.completion_price > 0]
        assert len(models_with_pricing) > 0, "At least some models should have pricing > 0"
        
        for model in models_with_pricing[:5]:  # Check first 5 priced models
            assert model.prompt_price >= 0, f"Prompt price should be non-negative for {model.id}"
            assert model.completion_price >= 0, f"Completion price should be non-negative for {model.id}"
            
            # Most models should have meaningful pricing (not exactly 0)
            if model.prompt_price > 0:
                assert model.prompt_price >= 0.01, f"Prompt price seems too low for {model.id}: ${model.prompt_price}"
            if model.completion_price > 0:
                assert model.completion_price >= 0.01, f"Completion price seems too low for {model.id}: ${model.completion_price}"
        
        print(f"✅ Pricing validation passed for {len(models_with_pricing)} models with pricing")
    
    async def test_model_capabilities_validation(self):
        """Test that model capabilities are properly parsed."""
        models = await list_models(limit=1000)
        
        # All programming models should support text input
        text_models = [m for m in models if m.has_text_input]
        assert len(text_models) == len(models), "All programming models should support text input"
        
        # Some models should support image input (vision capability)
        vision_models = [m for m in models if m.has_image_input]
        assert len(vision_models) > 0, "At least some models should support image input"
        assert len(vision_models) < len(models), "Not all models should support image input"
        
        print(f"✅ Capabilities validation: {len(text_models)} text, {len(vision_models)} vision")
    
    async def test_caching_behavior(self):
        """Test that model caching works correctly."""
        import time
        
        # First call - should fetch from API
        start_time = time.time()
        models1 = await list_models(force_refresh=True, limit=1000)
        first_duration = time.time() - start_time
        
        # Second call - should use cache
        start_time = time.time()
        models2 = await list_models(limit=1000)
        second_duration = time.time() - start_time
        
        # Validate caching worked
        assert len(models1) == len(models2), "Cached call should return same number of models"
        assert models1[0].id == models2[0].id, "Cached call should return same models"
        assert second_duration < first_duration, "Cached call should be faster"
        assert second_duration < 0.1, "Cached call should be very fast"
        
        print(f"✅ Caching: First call {first_duration:.2f}s, second call {second_duration:.2f}s")
    
    async def test_filter_models_empty_query(self):
        """Test filtering with empty query."""
        # Create sample models for testing
        models = [
            ModelInfo("anthropic/claude-sonnet-4", "Claude Sonnet 4", True, True, 3.0, 15.0, 1640995200),
            ModelInfo("openai/gpt-4", "GPT-4", True, False, 2.5, 10.0, 1640995200),
            ModelInfo("x-ai/grok-4", "Grok 4", True, True, 3.0, 15.0, 1640995200),
        ]
        
        # Test using list_models directly instead of separate filter function
        result = await list_models("", limit=5)
        # Just test that it returns models without filtering
        assert len(result) > 0, "Empty query should return models"
    
    async def test_filter_models_with_query(self):
        """Test filtering with search query."""
        # Test ID matching
        claude_results = await list_models("claude", limit=5)
        assert len(claude_results) >= 1, "Should find Claude models"
        assert all("claude" in m.id.lower() or "claude" in m.name.lower() for m in claude_results), "All results should match query"
        
        # Test name matching
        gpt_results = await list_models("gpt", limit=5)
        assert len(gpt_results) >= 1, "Should find GPT models"
        assert all("gpt" in m.id.lower() or "gpt" in m.name.lower() for m in gpt_results), "All results should match query"
    
    async def test_filter_models_vision_only(self):
        """Test vision-only filtering."""
        vision_results = await list_models("", vision_only=True, limit=5)
        assert len(vision_results) >= 1, "Should find vision models"
        assert all(m.has_image_input for m in vision_results), "All results should have image input"
        
        # Test vision + query
        vision_claude = await list_models("claude", vision_only=True, limit=5)
        if len(vision_claude) > 0:  # Only test if we find vision claude models
            assert all(m.has_image_input for m in vision_claude), "All results should have image input"
            assert all("claude" in m.id.lower() or "claude" in m.name.lower() for m in vision_claude), "All results should match query"
    
    async def test_filter_models_limit(self):
        """Test result limiting."""
        result = await list_models("", limit=3)
        assert len(result) == 3, "Should respect limit parameter"
        
        result_unlimited = await list_models("", limit=1000)
        assert len(result_unlimited) > 100, "Should return many models when limit is high"
    
    async def test_api_error_handling(self):
        """Test that API failures throw proper errors."""
        with patch('src.or_client._retry') as mock_retry:
            # Mock API failure
            mock_retry.side_effect = Exception("API connection failed")
            
            try:
                await list_models(force_refresh=True, limit=1000)
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "Failed to fetch models" in str(e)
        
        print("✅ API error handling works correctly")
    
    async def test_real_model_data_quality(self):
        """Test the quality of real model data from the API."""
        models = await list_models(limit=1000)
        
        # Check for expected model providers
        provider_ids = {model.id.split('/')[0] for model in models}
        expected_providers = {'anthropic', 'openai', 'x-ai', 'google'}
        
        found_providers = expected_providers.intersection(provider_ids)
        assert len(found_providers) > 0, f"Should find at least some expected providers. Found: {provider_ids}"
        
        # Check for reasonable model names
        model_names = [model.name for model in models]
        assert all(len(name) > 5 for name in model_names), "Model names should be descriptive"
        
        # Check that we have a mix of vision and text-only models
        vision_count = sum(1 for m in models if m.has_image_input)
        text_only_count = len(models) - vision_count
        
        assert vision_count > 0, "Should have some vision models"
        assert text_only_count > 0, "Should have some text-only models"
        
        print(f"✅ Data quality: {len(found_providers)} providers, {vision_count} vision, {text_only_count} text-only")


async def run_integration_tests():
    """Run all integration tests manually (for development)."""
    print("🧪 Running Model Loading Integration Tests")
    print("=" * 60)
    
    test_instance = TestModelLoading()
    
    try:
        print("🔄 Test 1: Basic model fetching...")
        await test_instance.test_fetch_programming_models_success()
        
        print("\n🔄 Test 2: Pricing validation...")
        await test_instance.test_model_pricing_validation()
        
        print("\n🔄 Test 3: Capabilities validation...")
        await test_instance.test_model_capabilities_validation()
        
        print("\n🔄 Test 4: Caching behavior...")
        await test_instance.test_caching_behavior()
        
        print("\n🔄 Test 5: Empty query filtering...")
        await test_instance.test_filter_models_empty_query()
        print("✅ Empty query filtering works")
        
        print("\n🔄 Test 6: Query filtering...")
        await test_instance.test_filter_models_with_query()
        print("✅ Query filtering works")
        
        print("\n🔄 Test 7: Vision filtering...")
        await test_instance.test_filter_models_vision_only()
        print("✅ Vision filtering works")
        
        print("\n🔄 Test 8: Result limiting...")
        await test_instance.test_filter_models_limit()
        print("✅ Result limiting works")
        
        print("\n🔄 Test 9: API error handling...")
        await test_instance.test_api_error_handling()
        
        print("\n🔄 Test 10: Real data quality...")
        await test_instance.test_real_model_data_quality()
        
        print("\n" + "=" * 60)
        print("🎉 ALL INTEGRATION TESTS PASSED!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Check environment
    required_vars = ['OPENROUTER_BASE_URL', 'OPENROUTER_API_KEY']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing environment variables: {', '.join(missing_vars)}")
        sys.exit(1)
    
    # Run tests
    success = asyncio.run(run_integration_tests())
    sys.exit(0 if success else 1)