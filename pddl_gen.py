import json
import argparse
from collections import defaultdict, deque
from unified_planning.shortcuts import *
from unified_planning.model.types import *
from unified_planning.io import PDDLWriter
import subprocess

# === 配置和加载 ===
with open("cared_recipies.json") as f:
    recipes_raw = json.load(f)
with open("cared_ingredients.json") as f:
    cared_ingredients = json.load(f)

def normalize(name):
    return name if name.startswith("minecraft:") else f"minecraft:{name}"

def get_result_item(recipe):
    r = recipe.get("result")
    return r if isinstance(r, str) else r.get("item")

def get_result_count(recipe):
    r = recipe.get("result")
    return 1 if isinstance(r, str) else r.get("count", 1)

recipes = {normalize(k): v for k, v in recipes_raw.items() if get_result_item(v)}

tag_map = {
    "minecraft:planks": [
        "minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
        "minecraft:jungle_planks", "minecraft:dark_oak_planks", "minecraft:acacia_planks"
    ]
}

# === 合成路径提取（精细级别） ===
def extract_primitive_steps(target, recipes):
    queue = deque()
    steps = []
    visited = defaultdict(int)
    queue.append((target, 1))

    while queue:
        current, qty = queue.popleft()
        visited[current] += qty
        recipe = recipes.get(current)
        if not recipe:
            for _ in range(qty):
                steps.append(("collect", current))
            continue

        ing_list = []
        if recipe["type"] == "minecraft:smelting":
            ing = recipe["ingredient"]
            item = normalize(ing.get("item") or tag_map.get(ing.get("tag"), ["minecraft:dirt"])[0])
            ing_list.append((item, qty))
            if current != "minecraft:furnace":
                ing_list.append(("minecraft:furnace", 1))
        elif recipe["type"] == "minecraft:crafting_shaped":
            pattern = recipe.get("pattern", [])
            key = recipe.get("key", {})
            ing_counter = defaultdict(int)
            for row in pattern:
                for c in row:
                    if c != " " and c in key:
                        ing = key[c]
                        item = normalize(ing.get("item") or tag_map.get(ing.get("tag"), ["minecraft:dirt"])[0])
                        ing_counter[item] += 1
            for ing, count in ing_counter.items():
                ing_list.append((ing, qty * count))
            if current != "minecraft:crafting_table":
                ing_list.append(("minecraft:crafting_table", 1))
        elif recipe["type"] == "minecraft:crafting_shapeless":
            for ing in recipe.get("ingredients", []):
                item = normalize(ing.get("item") or tag_map.get(ing.get("tag"), ["minecraft:dirt"])[0])
                ing_list.append((item, qty))
            if current != "minecraft:crafting_table":
                ing_list.append(("minecraft:crafting_table", 1))

        for ing, need_qty in ing_list:
            queue.append((ing, need_qty))
        for _ in range(qty):
            steps.append(("make", current))

    return steps[::-1]  # 顺序调整为 collect 在前

# === domain.pddl 和 problem.pddl 生成 ===
def write_domain_and_problem(target, steps):
    Item = UserType("item")
    count = Fluent("count", IntType(0, 9999), item=Item)
    problem = Problem("minecraft-domain")
    problem.add_fluent(count)
    all_items = set(s[1] for s in steps)
    obj_map = {i: Object(i.replace(":", "_"), Item) for i in all_items}
    for o in obj_map.values():
        problem.add_object(o)

    for i in all_items:
        action = InstantaneousAction(f"collect__{i.replace('minecraft:', '')}")
        itm = obj_map[i]
        action.add_increase_effect(count(itm), 1)
        problem.add_action(action)

    for name, recipe in recipes.items():
        if name not in all_items:
            continue
        result = obj_map[name]
        action = InstantaneousAction(f"make__{name.replace('minecraft:', '')}")
        if recipe["type"] == "minecraft:smelting":
            ing = normalize(recipe["ingredient"].get("item") or tag_map.get(recipe["ingredient"].get("tag"), ["minecraft:dirt"])[0])
            ing_obj = obj_map[ing]
            action.add_precondition(GE(count(ing_obj), 1))
            action.add_decrease_effect(count(ing_obj), 1)
            action.add_increase_effect(count(result), 1)
            if name != "minecraft:furnace":
                furnace = obj_map.get("minecraft:furnace")
                if furnace:
                    action.add_precondition(GE(count(furnace), 1))
        elif recipe["type"] in ["minecraft:crafting_shaped", "minecraft:crafting_shapeless"]:
            ingredients = defaultdict(int)
            if recipe["type"] == "minecraft:crafting_shaped":
                for row in recipe.get("pattern", []):
                    for c in row:
                        if c != " ":
                            v = recipe["key"].get(c)
                            if v:
                                ing = normalize(v.get("item") or tag_map.get(v.get("tag"), ["minecraft:dirt"])[0])
                                ingredients[ing] += 1
            else:
                for ing in recipe.get("ingredients", []):
                    item = normalize(ing.get("item") or tag_map.get(ing.get("tag"), ["minecraft:dirt"])[0])
                    ingredients[item] += 1

            for ing, qty in ingredients.items():
                ing_obj = obj_map[ing]
                action.add_precondition(GE(count(ing_obj), qty))
                action.add_decrease_effect(count(ing_obj), qty)
            action.add_increase_effect(count(result), get_result_count(recipe))
            if name != "minecraft:crafting_table":
                table = obj_map.get("minecraft:crafting_table")
                if table:
                    action.add_precondition(GE(count(table), 1))

        problem.add_action(action)

    for item in obj_map:
        init_val = 0
        problem.set_initial_value(count(obj_map[item]), init_val)
    goal = obj_map[target]
    problem.add_goal(GE(count(goal), 1))

    writer = PDDLWriter(problem)
    writer.write_domain("domain.pddl")
    writer.write_problem(f"problem_{target.replace('minecraft:', '')}.pddl")
    print("✅ domain.pddl 和 problem 已生成")

    # === 自动调用 ENHSP 获取 plan ===
    try:
        result = subprocess.run([
            "java", "-jar", "enhsp.jar", "-o", "domain.pddl", "-f", f"problem_{target.replace('minecraft:', '')}.pddl"
        ], capture_output=True, text=True, timeout=30)
        lines = result.stdout.splitlines()
        plan_lines = [l.strip() for l in lines if l.strip().startswith("0.0") or l.strip().startswith("(")]
        print("\n🧠 规划结果:")
        for line in plan_lines:
            print(line)
    except Exception as e:
        print("❌ ENHSP 执行失败:", e)

# === CLI 入口 ===
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=str)
    args = parser.parse_args()
    plan_steps = extract_primitive_steps(args.target, recipes)
    for s in plan_steps:
        print(f"- {s[0]} {s[1]}")
    write_domain_and_problem(args.target, plan_steps)