#!/bin/bash
# LangGraph Checkpointer Fix Patch Script

echo "🔧 Applying LangGraph checkpointer fix..."

# Fix task_generator_api.py
if [ -f "/app/task_generator_api.py" ]; then
    echo "Patching task_generator_api.py..."
    
    # Add the config parameter to graph.ainvoke call
    sed -i 's/result = await graph\.ainvoke({$/result = await graph.ainvoke({/' /app/task_generator_api.py
    sed -i '/result = await graph\.ainvoke({$/,/})$/c\        result = await graph.ainvoke({\
            "conversation_history": request.conversation_history,\
            "generated_task": None\
        }, config={"configurable": {"thread_id": f"task-gen-{hash(request.conversation_history) % 10000}"}})' /app/task_generator_api.py
    
    echo "✅ task_generator_api.py patched"
fi

# Fix task_solver_agent.py  
if [ -f "/app/task_solver_agent.py" ]; then
    echo "Patching task_solver_agent.py..."
    
    # Add the config parameter to graph.ainvoke call
    sed -i 's/result = await self\.graph\.ainvoke(state)$/result = await self.graph.ainvoke(state, config={"configurable": {"thread_id": f"task-solver-{task_id}"}})/' /app/task_solver_agent.py
    
    echo "✅ task_solver_agent.py patched"
fi

echo "🎯 Patch complete! Restarting service..."